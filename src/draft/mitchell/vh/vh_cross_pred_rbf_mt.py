import os

id = int(os.getenv("SGE_TASK_ID", 0))
first = int(os.getenv("SGE_TASK_FIRST", 0))
last = int(os.getenv("SGE_TASK_LAST", 0))
print("ID {0}".format(id))
print("Task %d of %d tasks, starting with %d." % (id, last - first + 1, first))

print("This job was submitted from %s, it is currently running on %s" % (os.getenv("SGE_O_HOST"), os.getenv("HOSTNAME")))

print("NHOSTS: %s, NSLOTS: %s" % (os.getenv("NHOSTS"), os.getenv("NSLOTS")))

import sys
print(sys.version)





#get V animation data -> [N, 150, 150]
#create 2d delay coordinates -> [N, 150, 150, d]
#create new dataset with small data groups -> [N, 150, 150, d*sigma*sigma]
#create d*sigma*sigma-k tree from this data
#search nearest neighbours (1 or 2) and predict new U value

import os,sys,inspect
currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
parentdir = os.path.dirname(currentdir)
grandparentdir = os.path.dirname(parentdir)
sys.path.insert(0, parentdir)
sys.path.insert(0, grandparentdir)

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.animation as animation
import progressbar
import dill as pickle
from scipy.spatial import KDTree
from sklearn.neighbors import NearestNeighbors as NN
from helper import *

from multiprocessing import Process, Queue, Manager, Pool #we require Pathos version >=0.2.6. Otherwise we will get an "EOFError: Ran out of input" exception
#from pathos.multiprocessing import Pool
import multiprocessing
import ctypes

from multiprocessing import process

process.current_process()._config['tempdir'] =  '/dev/shm/' #'/data.bmp/roland/temp/'

N = 150
ndata = 30000
sigma = 5
sigma_skip = 2
eff_sigma = int(np.ceil(sigma/sigma_skip))
ddim = 3
patch_radius = sigma//2
trainLength = 29000
testLength = 1000

m = trainLength//50
n = trainLength
nprime = 1
samplingDist = n//m
def setupArrays():
    global shared_h_data_base, shared_v_data_base, shared_prediction_base
    global shared_h_data, shared_v_data, shared_prediction

    print("setting up arrays...")
    shared_h_data_base = multiprocessing.Array(ctypes.c_double, ndata*N*N)
    shared_h_data = np.ctypeslib.as_array(shared_h_data_base.get_obj())
    shared_h_data = shared_h_data.reshape(-1, N, N)
   
    shared_v_data_base = multiprocessing.Array(ctypes.c_double, ndata*N*N)
    shared_v_data = np.ctypeslib.as_array(shared_v_data_base.get_obj())
    shared_v_data = shared_v_data.reshape(-1, N, N)
    
    shared_prediction_base = multiprocessing.Array(ctypes.c_double, testLength*N*N)
    shared_prediction = np.ctypeslib.as_array(shared_prediction_base.get_obj())
    shared_prediction = shared_prediction.reshape(-1, N, N)
    print("setting up finished") 

setupArrays()

def create_2d_delay_coordinates(data, delay_dimension, tau):
    result = np.repeat(data[:, :, :, np.newaxis], repeats=delay_dimension, axis=3)

    for n in range(1, delay_dimension):
        result[:, :, :, n] = np.roll(result[:, :, :, n], n*tau, axis=0)
    result[0:delay_dimension-1,:,:] = 0

    return result
    
def create_0d_delay_coordinates(data, delay_dimension, tau):
    result = np.repeat(data[:, np.newaxis], repeats=delay_dimension, axis=1)
    
    for n in range(1, delay_dimension):
        result[:, n] = np.roll(result[:, n], n*tau, axis=0)
    result[0:delay_dimension-1,:] = 0

    return result

def generate_data(N, trans, sample_rate, Ngrid):
    data = None
    if (os.path.exists("../cache/raw/{0}_{1}.vh.dat.npy".format(N, Ngrid)) == False):
        print("generating data...")
        data = generate_hh_data(N, 20000, 50, Ngrid=Ngrid)
        np.save("../cache/raw/{0}_{1}.vh.dat.npy".format(N, Ngrid), data)
        print("generating finished")
    else:
        print("loading data...")
        data = np.load("../cache/raw/{0}_{1}.vh.dat.npy".format(N, Ngrid))
        print("loading finished")
        
        """
        #at the moment we are doing a h -> v cross prediction.
        #switch the entries for the v -> h prediction
        tmp = data[0].copy()
        data[0] = data[1].copy()
        data[1] = tmp.copy()
        """
        
        
    return data

def get_prediction(data):
    y, x = data
    
    pred = None
    if (y < patch_radius or y >= N-patch_radius or x < patch_radius or x >= N-patch_radius):
        pred = predict_frame_pixel(data)
    else:
        pred = predict_inner_pixel(data)
    get_prediction.q.put((y, x, pred))

def rbf(xi, yi, sigmam):
    return np.exp(-np.sum((xi-yi)**2)/(2*sigmam**2))

def rbf_hec(xi, yi, sigmam):
    xi = np.tile(xi, (len(yi),1))
    return np.exp(-np.sum((xi-yi)**2, axis=1)/(2*sigmam**2))
    
def predict_frame_pixel(data, def_param=(shared_h_data, shared_v_data)):
    y, x = data
   
    shared_delayed_h_data = create_0d_delay_coordinates(shared_h_data[:, y, x], ddim, tau=32)
   
    delayed_patched_h_data_train = shared_delayed_h_data[:trainLength]
    u_data_train = shared_v_data[:trainLength, y, x]

    delayed_patched_h_data_test = shared_delayed_h_data[trainLength:trainLength+testLength]
    u_data_test = shared_v_data[trainLength:trainLength+testLength, y, x]

    flat_h_data_train = delayed_patched_h_data_train.reshape(-1, ddim)
    flat_v_data_train = u_data_train.reshape(-1,1)

    flat_h_data_test = delayed_patched_h_data_test.reshape(-1, ddim)
    flat_v_data_test = u_data_test.reshape(-1,1)

    samplingPoints = flat_h_data_train[0::samplingDist]
    sigmam = np.ones(m)*5

    #construct matrices
    A = np.empty((n, m))
    F = np.empty((n, nprime))
    L = None

    #construct target matrix F
    #F = flat_v_data_train.copy()

    #construct trainig matrix A
    for i in range(n):
        A[i] = rbf_hec(flat_h_data_train[i], samplingPoints, sigmam)

    #calculate the resulting weights L
    APINV = np.linalg.pinv(A)
    L = np.dot(APINV, flat_v_data_train) #F

    Lt = L.T

    flat_prediction = np.zeros((testLength, nprime))

    for i in range(0, flat_prediction.shape[0]):
        flat_prediction[i] = np.dot(Lt, rbf_hec(flat_h_data_test[i], samplingPoints, sigmam))

    pred = flat_prediction.ravel()
    
    return pred    
    
def predict_inner_pixel(data, def_param=(shared_h_data, shared_v_data)):
    y, x = data
    
    shared_delayed_h_data = create_2d_delay_coordinates(shared_h_data[:, y-patch_radius:y+patch_radius+1, x-patch_radius:x+patch_radius+1][:, ::sigma_skip, ::sigma_skip], ddim, tau=32)
    #print(shared_delayed_h_data.shape)
    #print(ddim*eff_sigma*eff_sigma)
    shared_delayed_patched_h_data = np.empty((ndata, 1, 1, ddim*eff_sigma*eff_sigma))
    shared_delayed_patched_h_data[:, 0, 0] = shared_delayed_h_data.reshape(-1, ddim*eff_sigma*eff_sigma)
    
    delayed_patched_h_data_train = shared_delayed_patched_h_data[:trainLength, 0, 0]
    u_data_train = shared_v_data[:trainLength, y, x]

    delayed_patched_h_data_test = shared_delayed_patched_h_data[trainLength:trainLength+testLength, 0, 0]
    u_data_test = shared_v_data[trainLength:trainLength+testLength, y, x]

    flat_h_data_train = delayed_patched_h_data_train.reshape(-1, shared_delayed_patched_h_data.shape[3])
    flat_v_data_train = u_data_train.reshape(-1,1)

    flat_h_data_test = delayed_patched_h_data_test.reshape(-1, shared_delayed_patched_h_data.shape[3])
    flat_v_data_test = u_data_test.reshape(-1,1)
    
    samplingPoints = flat_h_data_train[0::samplingDist]
    sigmam = np.ones(m)*5

    #construct matrices
    A = np.empty((n, m))
    F = np.empty((n, nprime))
    L = None

    #construct target matrix F
    #F = flat_v_data_train.copy()

    #construct trainig matrix A
    for i in range(n):
        A[i] = rbf_hec(flat_h_data_train[i], samplingPoints, sigmam)

    #calculate the resulting weights L
    APINV = np.linalg.pinv(A)
    L = np.dot(APINV, flat_v_data_train) #F

    Lt = L.T

    flat_prediction = np.zeros((testLength, nprime))

    for i in range(0, flat_prediction.shape[0]):
        flat_prediction[i] = np.dot(Lt, rbf_hec(flat_h_data_test[i], samplingPoints, sigmam))

    pred = flat_prediction.ravel()
    
    return pred       

def processThreadResults(threadname, q, numberOfWorkers, numberOfResults, def_param=(shared_prediction, shared_v_data)):
    global prediction

    bar = progressbar.ProgressBar(max_halue=numberOfResults, redirect_stdout=True, poll_interval=0.0001)
    bar.update(0)

    finishedWorkers = 0
    finishedResults = 0

    while True:
        if (finishedResults == numberOfResults):
            return

        newData= q.get()
        finishedResults += 1
        ind_y, ind_x, data = newData

        shared_prediction[:, ind_y, ind_x] = data

        bar.update(finishedResults)

def get_prediction_init(q):
    get_prediction.q = q

def mainFunction():
    if (trainLength + testLength > ndata):
        print("Please adjust the trainig and testing phase length!")
        exit()
    
    delayed_patched_h_data = None
    u_data = None
    print("generating data...")
    u_data_t, v_data_t = generate_data(ndata, 50000, 50, Ngrid=N)#, delay_dimension=ddim, patch_size=sigma)
    shared_h_data[:] = v_data_t[:]
    shared_v_data[:] = u_data_t[:]
    print("generation finished")

    queue = Queue() # use manager.queue() ?
    print("preparing threads...")
    pool = Pool(processes=16, initializer=get_prediction_init, initargs=[queue,])

    processProcessResultsThread = Process(target=processThreadResults, args=("processProcessResultsThread", queue, 16, N*N)) #*N

    modifyDataProcessList = []
    jobs = []
    for y in range(N):
        for x in range(N): #N
                jobs.append((y, x))

    ###print("fitting...")
    processProcessResultsThread.start()
    results = pool.map(get_prediction, jobs)
    pool.close()

    processProcessResultsThread.join()

    ###print("finished fitting")
    
    shared_prediction[shared_prediction > 1.0] = 1.0
    shared_prediction[shared_prediction < 0.0] = 0.0
    
    diff = (shared_v_data[trainLength:]-shared_prediction)
    mse = np.mean((diff)**2)
    print("test error: {0}".format(mse))
    print("inner test error: {0}".format(np.mean(((shared_v_data[trainLength:]-shared_prediction)[patch_radius:N-patch_radius, patch_radius:N-patch_radius])**2)))

    viewData = [("Orig", shared_v_data[trainLength:]), ("Pred", shared_prediction), ("Source", shared_h_data[trainLength:]), ("Diff", diff)]
    f = open("../cache/viewdata/htov/nn_viewdata_{0}_{1}_{2}_{3}_{4}.dat".format(ndata, sigma, sigma_skip, ddim, k), "wb")
    pickle.dump(viewData, f)
    f.close()

    #show_results({"Orig" : shared_v_data[trainLength:], "Pred" : shared_prediction, "Diff" : diff})
    
    print("done")


if __name__== '__main__':
    mainFunction()

