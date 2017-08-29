from operator import itemgetter
import itertools
import numpy as np
import math
from scipy.linalg import lu, qr, solve_triangular, inv, solve, svd, qr_multiply
from numpy.linalg import cond
from groebner.polynomial import Polynomial, MultiCheb, MultiPower
from scipy.sparse import csc_matrix, vstack
from groebner.utils import Term, row_swap_matrix, fill_size, clean_zeros_from_matrix, triangular_solve, divides, get_var_list, TVBError, slice_top, get_var_list
import matplotlib.pyplot as plt
from collections import defaultdict
import gc
import time

def TelenVanBarel(initial_poly_list, accuracy = 1.e-10):
    """
    Macaulay will take a list of polynomials and use them to construct a Macaulay matrix.

    Parameters
    --------
    initial_poly_list: A list of polynomials
    accuracy: How small we want a number to be before assuming it is zero.
    --------

    Returns
    -----------
    Reduced Macaulay matrix that can be passed into the root finder.
    -----------
    """
    Power = bool
    if all([type(p) == MultiPower for p in initial_poly_list]):
        Power = True
    elif all([type(p) == MultiCheb for p in initial_poly_list]):
        Power = False
    else:
        print([type(p) == MultiPower for p in initial_poly_list])
        raise ValueError('Bad polynomials in list')

    poly_coeff_list = []
    degree = find_degree(initial_poly_list)
    dim = initial_poly_list[0].dim
    
    #Checks to make sure TVB will work.
    if not has_top_xs(initial_poly_list):
        raise TVBError("Doesn't have all x^n's on diagonal. Do linear transformation")
    S = get_S_Poly(initial_poly_list)
    if isinstance(S,Polynomial):
        initial_poly_list.append(S)
        #degree = find_degree(initial_poly_list)
    
    for i in initial_poly_list:
        poly_coeff_list = add_polys(degree, i, poly_coeff_list)

    matrix, matrix_terms, matrix_shape_stuff = create_matrix(poly_coeff_list, degree, dim)
        
    matrix, matrix_terms = rrqr_reduceTelenVanBarel2(matrix, matrix_terms, matrix_shape_stuff, 
                                                        accuracy = accuracy)    
    matrix = clean_zeros_from_matrix(matrix)
    
    matrix, matrix_terms = triangular_solve(matrix, matrix_terms, reorder = False)
    matrix = clean_zeros_from_matrix(matrix)

    VB = matrix_terms[matrix.shape[0]:]
    basisDict = makeBasisDict(matrix, matrix_terms, VB)
    return basisDict, VB

def makeBasisDict(matrix, matrix_terms, VB):
    '''
    Take a matrix that has been traingular solved and returns a dictionary mapping the pivot columns terms
    behind them, all of which will be in the vector basis. All the matrixes that are mapped to will be the same shape.
    '''
    remainder_shape = np.maximum.reduce([mon for mon in VB])
    remainder_shape += np.ones_like(remainder_shape)
    basisDict = {}

    spots = list()
    for dim in range(matrix_terms.shape[1]):
        spots.append(matrix_terms[matrix.shape[0]:].T[dim])

    for i in range(matrix.shape[0]):
        remainder = np.zeros(remainder_shape)
        row = matrix[i]
        remainder[spots] = row[matrix.shape[0]:]
        basisDict[tuple(matrix_terms[i])] = remainder

    return basisDict

def find_degree(poly_list):
    '''
    Takes a list of polynomials and finds the degree needed for a Macaulay matrix.
    Adds the degree of each polynomial and then subtracts the total number of polynomials and adds one.

    Example:
        For polynomials [P1,P2,P3] with degree [d1,d2,d3] the function returns d1+d2+d3-3+1
    '''
    degree_needed = 0
    for poly in poly_list:
        degree_needed += poly.degree
    return ((degree_needed - len(poly_list)) + 1)

def mon_combosHighest(mon, numLeft, spot = 0):
    '''
    Same as mon_combos but only returns highest degree stuff.
    '''
    answers = list()
    if len(mon) == spot+1: #We are at the end of mon, no more recursion.
        mon[spot] = numLeft
        answers.append(mon.copy())
        return answers
    if numLeft == 0: #Nothing else can be added.
        answers.append(mon.copy())
        return answers
    temp = mon.copy() #Quicker than copying every time inside the loop.
    for i in range(numLeft+1): #Recursively add to mon further down.
        temp[spot] = i
        answers += mon_combosHighest(temp, numLeft-i, spot+1)
    return answers

def mon_combos(mon, numLeft, spot = 0):
    '''
    This function finds all the monomials up to a given degree (here numLeft) and returns them.
    mon is a tuple that starts as all 0's and gets changed as needed to get all the monomials.
    numLeft starts as the dimension, but as the code goes is how much can still be added to mon.
    spot is the place in mon we are currently adding things to.
    Returns a list of all the possible monomials.
    '''
    answers = list()
    if len(mon) == spot+1: #We are at the end of mon, no more recursion.
        for i in range(numLeft+1):
            mon[spot] = i
            answers.append(mon.copy())
        return answers
    if numLeft == 0: #Nothing else can be added.
        answers.append(mon.copy())
        return answers
    temp = mon.copy() #Quicker than copying every time inside the loop.
    for i in range(numLeft+1): #Recursively add to mon further down.
        temp[spot] = i
        answers += mon_combos(temp, numLeft-i, spot+1)
    return answers

def add_polys(degree, poly, poly_coeff_list):
    """
    Take each polynomial and adds it to a poly_list
    Then uses monomial multiplication and adds all polynomials with degree less than
        or equal to the total degree needed.
    Returns a list of polynomials.
    """
    poly_coeff_list.append(poly.coeff)
    deg = degree - poly.degree
    dim = poly.dim
    mons = mon_combos(np.zeros(dim, dtype = int),deg)
    mons = mons[1:]
    for i in mons:
        poly_coeff_list.append(poly.mon_mult(i, returnType = 'Matrix'))
    return poly_coeff_list

def sorted_matrix_terms(degree, dim):
    '''Finds the matrix_terms sorted in the term order needed for TelenVanBarel reduction.
    So the highest terms come first,the x,y,z etc monomials last.
    Parameters
    ----------
    degree : int
        The degree of the TVB Matrix
    dim : int
        The dimension of the polynomials going into the matrix.
    Returns
    -------
    matrix_terms : numpy array
        The sorted matrix_terms.
    matrix_term_stuff : tuple
        The first entry is the number of 'highest' monomial terms. The second entry is the number of 'other' terms,
        those not in the first or third catagory. The third entry is the number of monomials of degree one of a
        single variable, as well as the monomial 1.
    '''
    highest_mons = mon_combosHighest(np.zeros(dim, dtype = int),degree)
    highest = np.vstack(highest_mons)
    
    other_mons = list()
    d = degree - 1
    while d > 1:
        other_mons += mon_combosHighest(np.zeros(dim, dtype = int),d)
        d -= 1
    others = np.vstack(other_mons)
    
    xs_mons = mon_combos(np.zeros(dim, dtype = int),1)
    xs = np.vstack(xs_mons)
    
    sorted_matrix_terms = np.vstack((highest,others,xs))
    return sorted_matrix_terms, tuple([len(highest),len(others),len(xs)])

def create_matrix(poly_coeffs, degree, dim):
    ''' Builds a Telen Van Barel matrix.

    Parameters
    ----------
    poly_coeffs : list.
        Contains numpy arrays that hold the coefficients of the polynomials to be put in the matrix.
    degree : int
        The degree of the TVB Matrix
    dim : int
        The dimension of the polynomials going into the matrix.
    Returns
    -------
    matrix : 2D numpy array
        The Telen Van Barel matrix.
    '''
    bigShape = np.maximum.reduce([p.shape for p in poly_coeffs])

    matrix_terms, matrix_shape_stuff = sorted_matrix_terms(degree, dim)

    #Get the slices needed to pull the matrix_terms from the coeff matrix.
    matrix_term_indexes = list()
    for i in range(len(bigShape)):
        matrix_term_indexes.append(matrix_terms.T[i])

    #Adds the poly_coeffs to flat_polys, using added_zeros to make sure every term is in there.
    added_zeros = np.zeros(bigShape)
    flat_polys = list()
    for coeff in poly_coeffs:
        slices = slice_top(coeff)
        added_zeros[slices] = coeff
        flat_polys.append(added_zeros[matrix_term_indexes])
        added_zeros[slices] = np.zeros_like(coeff)

    #Make the matrix
    matrix = np.vstack(flat_polys[::-1])
    
    if matrix_shape_stuff[0] > matrix.shape[0]: #The matrix isn't tall enough, these can't all be pivot columns.
        raise TVBError("HIGHEST NOT FULL RANK. TRY HIGHER DEGREE")
    
    #Sorts the rows of the matrix so it is close to upper triangular.
    matrix = row_swap_matrix(matrix)
    return matrix, matrix_terms, matrix_shape_stuff

def rrqr_reduceTelenVanBarel(matrix, matrix_terms, matrix_shape_stuff, accuracy = 1.e-10):
    ''' Reduces a Telen Van Barel Macaulay matrix.

    The matrix is split into the shape
    A B C
    D E F
    Where A is square and contains all the highest terms, and C contains all the x,y,z etc. terms. The lengths
    are determined by the matrix_shape_stuff tuple. First A and D are reduced using rrqr, and then the rest of
    the matrix is multiplied by Q.T to change it accordingly. Then E is reduced by rrqr, the rows of B are shifted
    accordingly, and F is multipled by Q.T to change it accordingly. This is all done in place to save memory.

    Parameters
    ----------
    matrix : numpy array.
        The Macaulay matrix, sorted in TVB style.
    matrix_terms: numpy array
        Each row of the array contains a term in the matrix. The i'th row corresponds to
        the i'th column in the matrix.
    matrix_shape_stuff : tuple
        Terrible name I know. It has 3 values, the first is how many columnns are in the
        'highest' part of the matrix. The second is how many are in the 'others' part of
        the matrix, and the third is how many are in the 'xs' part.
    Returns
    -------
    matrix : numpy array
        The reduced matrix.
    matrix_terms: numpy array
        The resorted matrix_terms.
    '''
    highest_num = matrix_shape_stuff[0]
    others_num = matrix_shape_stuff[1]
    xs_num = matrix_shape_stuff[2]
        
    #RRQR reduces A and D sticking the result in it's place.
    Q1,matrix[:,:highest_num],P1 = qr(matrix[:,:highest_num], pivoting = True)
    
    #if abs(matrix[:,:highest_num].diagonal()[-1]) < accuracy:
    #    raise TVBError("HIGHEST NOT FULL RANK")
            
    #Multiplying the rest of the matrix by Q.T
    matrix[:,highest_num:] = Q1.T@matrix[:,highest_num:]
    Q1 = 0 #Get rid of Q1 for memory purposes.

    #RRQR reduces E sticking the result in it's place.
    Q,matrix[highest_num:,highest_num:highest_num+others_num],P = qr(matrix[highest_num:,highest_num:highest_num+others_num], pivoting = True)

    #Multiplies F by Q.T.
    matrix[highest_num:,highest_num+others_num:] = Q.T@matrix[highest_num:,highest_num+others_num:]
    Q = 0 #Get rid of Q for memory purposes.

    #Shifts the columns of B
    matrix[:highest_num,highest_num:highest_num+others_num] = matrix[:highest_num,highest_num:highest_num+others_num][:,P]

    #Checks for 0 rows and gets rid of them.
    non_zero_rows = list()
    for i in range(min(highest_num+others_num, matrix.shape[0])):
        if np.abs(matrix[i][i]) > accuracy:
            non_zero_rows.append(i)
    matrix = matrix[non_zero_rows,:]

    #Resorts the matrix_terms.
    matrix_terms[:highest_num] = matrix_terms[:highest_num][P1]
    matrix_terms[highest_num:highest_num+others_num] = matrix_terms[highest_num:highest_num+others_num][P]

    return matrix, matrix_terms


def rrqr_reduceTelenVanBarel2(matrix, matrix_terms, matrix_shape_stuff, accuracy = 1.e-10):
    ''' Reduces a Telen Van Barel Macaulay matrix.

    This function does the same thing as rrqr_reduceTelenVanBarel but uses qr_multiply instead of qr and a multiplication
    to make the function faster and more memory efficient.

    Parameters
    ----------
    matrix : numpy array.
        The Macaulay matrix, sorted in TVB style.
    matrix_terms: numpy array
        Each row of the array contains a term in the matrix. The i'th row corresponds to
        the i'th column in the matrix.
    matrix_shape_stuff : tuple
        Terrible name I know. It has 3 values, the first is how many columnns are in the
        'highest' part of the matrix. The second is how many are in the 'others' part of
        the matrix, and the third is how many are in the 'xs' part.
    accuracy : float
        What is determined to be 0.
    Returns
    -------
    matrix : numpy array
        The reduced matrix.
    matrix_terms: numpy array
        The resorted matrix_terms.
    '''
    highest_num = matrix_shape_stuff[0]
    others_num = matrix_shape_stuff[1]
    xs_num = matrix_shape_stuff[2]
    
    C1,matrix[:highest_num,:highest_num],P1 = qr_multiply(matrix[:,:highest_num], matrix[:,highest_num:].T, mode = 'right', pivoting = True)
    matrix[:highest_num,highest_num:] = C1.T
    C1 = 0
    #print(matrix_terms[:highest_num][P1])
    #print(matrix[:,:highest_num].diagonal())
    if abs(matrix[:,:highest_num].diagonal()[-1]) < accuracy:
        raise TVBError("HIGHEST NOT FULL RANK")
    
    matrix[:highest_num,highest_num:] = solve_triangular(matrix[:highest_num,:highest_num],matrix[:highest_num,highest_num:])
    matrix[:highest_num,:highest_num] = np.eye(highest_num)
    matrix[highest_num:,highest_num:] -= (matrix[highest_num:,:highest_num][:,P1])@matrix[:highest_num,highest_num:]
    matrix_terms[:highest_num] = matrix_terms[:highest_num][P1]
    P1 = 0

    C,R,P = qr_multiply(matrix[highest_num:,highest_num:highest_num+others_num], matrix[highest_num:,highest_num+others_num:].T, mode = 'right', pivoting = True)
    matrix = np.vstack((matrix[:highest_num],np.hstack((np.zeros_like(matrix[highest_num:R.shape[0]+highest_num,:highest_num]),R,C.T))))
    C,R = 0,0
    
    #Shifts the columns of B
    matrix[:highest_num,highest_num:highest_num+others_num] = matrix[:highest_num,highest_num:highest_num+others_num][:,P]
    matrix_terms[highest_num:highest_num+others_num] = matrix_terms[highest_num:highest_num+others_num][P]
    P = 0

    #Checks for 0 rows and gets rid of them.
    non_zero_rows = list()
    for i in range(min(highest_num+others_num, matrix.shape[0])):
        if np.abs(matrix[i][i]) > accuracy:
            non_zero_rows.append(i)
    matrix = matrix[non_zero_rows,:]

    return matrix, matrix_terms


def has_top_xs(polys):
    '''Finds out if the Macaulay Matrix will have an x^d in each dimension.
    
    TVB redction will work if an only if this is true. So in 2 dimensions a Macaulay matrix of degree d
    needs to have a x^d and y^d in it, in 3 dimensions it needs an x^d, y^d and z^d etc.
    
    Parameters
    ----------
    polys : list
        The polynomials with which the Macaulay Matrix is created.
    Returns
    -------
    value : bool
        Whether or not it has them all.
    '''
    dim = polys[0].dim
    
    hasXs = np.zeros(dim)
    #Make everything behind the diagonal 0,
    for poly in polys:
        deg = poly.degree
        
        possibleXs = set()
        for row in deg*get_var_list(dim):
            possibleXs.add(tuple(deg*np.array(row)))
        
        for mon in zip(*np.where(poly.coeff!=0)):
            #Checks to see if it's an x^n.
            if mon in possibleXs:
                hasXs += mon
    return np.all(hasXs)

def getDiagPoly(poly):
    '''Gets the diagonal polynomial of a polynomial.
    
    This is defined as only the monomials in a polynomial that are of the highest degree. Everything else is 0.
        
    Parameters
    ----------
    poly : Polynomial
        The polynomial of interest.
    Returns
    -------
    poly : Polynomial
        The diagonal polynomial.
    '''
    diagCoeff = poly.coeff.copy()
    deg = poly.degree
    for mon in zip(*np.where(diagCoeff!=0)):
        if np.sum(mon) != deg:
            diagCoeff[mon] = 0
    if isinstance(poly,MultiPower):
        return MultiPower(diagCoeff)
    else:
        return MultiCheb(diagCoeff)

def topDegreeMatrix(polys, degree):
    '''Gets the upper left corner of a Macaulay Matrix, the top degree part.
    
    Only includes the columns that are monomials of highest degree and the rows that have non-zero elements in those columns
    
    Parameters
    ----------
    polys : list
        The polynomials used to make the matrix.
    degree : int
        The degree of the Macaulay Matrix to be made.
    Returns
    -------
    matrix : numpy array
        The matrix.
    matrixMons : list
        A list of the monomials that were used to create the matrix. The i'th element is the monomial used to create the
        i'th row of the matrix.
    full : numpy array
        An array of zeros with the shape of the degree of the matrix in each dimension.
    '''
    dim = polys[0].dim
    power = isinstance(polys[0],MultiPower)
    
    diagPolys = list()
    for poly in polys:
        diagPolys.append(getDiagPoly(poly))


    diagSpots = np.vstack(mon_combosHighest(np.zeros(dim, dtype = int),degree))
    diagPlaces = list()
    for i in range(dim):
        diagPlaces.append(diagSpots.T[i])

    full = np.zeros((degree+1)*np.ones(dim, dtype = int))
    matrixRows = list()
    matrixMons = list()
    for diagPoly in diagPolys:
        mons = mon_combosHighest(np.zeros(dim, dtype = int),degree - diagPoly.coeff.shape[0]+1)
        matrixMons.append(mons)
        for mon in mons:
            coeff = diagPoly.mon_mult(mon, returnType = 'Matrix')
            full[slice_top(coeff)] = coeff
            matrixRows.append(full[diagPlaces])
            full[slice_top(coeff)] = np.zeros_like(coeff)
    matrix = np.vstack(matrixRows)
    return matrix, matrixMons, full

def getFPolys(fcoeffs, matrixMons, full, power):
    fPolys = list()
    for mons in matrixMons:
        fCoeff = full.copy()
        for mon in mons:
            fCoeff[tuple(mon)] = fcoeffs[0]
            fcoeffs = fcoeffs[1:]
        if power:
            fPolys.append(MultiPower(fCoeff))
        else:
            fPolys.append(MultiCheb(fCoeff))
    return fPolys

def finalizeS(polys, S):
    '''
    Takes in polys and S, makes sure S actually works. If not, it finds an S that does.
    '''
    if S.degree <= 0:
        raise TVBError('Polys are non-zero dimensional')
    
    dim = polys[0].dim
    power = isinstance(polys[0],MultiPower)
    degree = find_degree(polys)
    
    matrix, matrixMons, full = topDegreeMatrix(polys+list([S]), degree)
    Q,R,P = qr(matrix, pivoting = True)
    if abs(R.diagonal()[-1]) > 1.e-10:
        return S
    
    fPolys = getFPolys(clean_zeros_from_matrix(Q.T[-1]), matrixMons, full, power)
    if power:
        S2 = MultiPower(np.array([0]))
    else:
        S2 = MultiCheb(np.array([0]))
    for i in range(len(polys)):
        poly = polys[i]
        f = fPolys[i]
        S2 += poly*f
    S2 += S*fPolys[-1]
    S2.__init__(clean_zeros_from_matrix(S2.coeff))
    return finalizeS(polys, S2)

def get_S_Poly(polys):
    dim = polys[0].dim
    power = isinstance(polys[0],MultiPower)
    degree = find_degree(polys)
    
    matrix, matrixMons, full = topDegreeMatrix(polys, degree)

    #print(matrix)
    Q,R,P = qr(matrix, pivoting = True)
    #print(R)
    if abs(R.diagonal()[-1]) > 1.e-10:
        return -1 #It works fine.
    fPolys = getFPolys(clean_zeros_from_matrix(Q.T[-1]), matrixMons, full, power)
    if power:
        S = MultiPower(np.array([0]))
    else:
        S = MultiCheb(np.array([0]))
    for i in range(len(polys)):
        poly = polys[i]
        f = fPolys[i]
        S += poly*f
    #S.__init__(clean_zeros_from_matrix(S.coeff))
    
    #Now make a new function to check if it's done and if not keep going.
    return finalizeS(polys, S)


