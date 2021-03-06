"""
    Performs a cross prediction of the first/second variable by using the second/first variable of the model. All constants etc. must be set before
    by the corresponding *_p.py file.
"""

import os
import sys
sys.path.insert(1, os.path.join(sys.path[0], '../..'))
sys.path.insert(1, os.path.join(sys.path[0], '../../barkley'))
sys.path.insert(1, os.path.join(sys.path[0], '../../mitchell'))
sys.path.insert(1, os.path.join(sys.path[0], '../../bocf'))

import numpy as np
#import matplotlib.pyplot as plt
#import matplotlib.animation as animation
import progressbar
import dill as pickle

from ESN import ESN
from RBF import RBF
from NN import NN

from multiprocessing import Process, Queue, Manager, Pool #we require Pathos version >=0.2.6. Otherwise we will get an "EOFError: Ran out of input" exception
import multiprocessing
import ctypes
from multiprocessing import process
from multiprocessing import Value

from helper import *
import bocf_helper as bocfh
import barkley_helper as bh
import mitchell_helper as mh

import cross_prediction_avg_settings as cpmtps

from numpy.linalg.linalg import LinAlgError
#get V animation data -> [N, 150, 150]
#create 2d delay coordinates -> [N, 150, 150, d]
#create new dataset with small data groups -> [N, 150, 150, d*sigma*sigma]
#create d*sigma*sigma-k tree from this data
#search nearest neighbours (1 or 2) and predict new U value

#set the temporary buffer for the multiprocessing module manually to the shm
#to solve "no enough space"-problems
process.current_process()._config['tempdir'] =  '/dev/shm/'

tau = cpmtps.tau
N = cpmtps.N
ndata = cpmtps.ndata
trainLength = cpmtps.trainLength
predictionLength = cpmtps.predictionLength
testLength = cpmtps.testLength
#the testLength is included in the predictionLength => testLength < predictionLength, and predictionLength-testLength is the validation length

useInputScaling = False

#will be set by the *_p.py file
direction, prediction_mode, patch_radius, eff_sigma, sigma, sigma_skip = None, None, None, None, None, None,
k, width, basis_points, ddim = None, None, None, None
noise = None
n_units, spectral_radius, leaking_rate, random_seed, noise_level, regression_parameter, sparseness = None, None, None, None, None, None, None

def load_settings():
    global direction, prediction_mode, patch_radius, eff_sigma, sigma, sigma_skip, k, width, basis_points, ddim, n_units
    global spectral_radius, leaking_rate, random_seed, noise_level, regression_parameter, sparseness
    global noise

    direction = cpmtps.direction
    prediction_mode = cpmtps.prediction_mode
    patch_radius = cpmtps.patch_radius
    eff_sigma = cpmtps.eff_sigma
    sigma = cpmtps.sigma
    sigma_skip = cpmtps.sigma_skip
    k = cpmtps.k
    width = cpmtps.width
    basis_points = cpmtps.basis_points
    ddim = cpmtps.ddim
    n_units = cpmtps.n_units
    spectral_radius = cpmtps.spectral_radius
    leaking_rate = cpmtps.leaking_rate
    random_seed = cpmtps.random_seed
    noise_level = cpmtps.noise_level
    regression_parameter = cpmtps.regression_parameter
    sparseness = cpmtps.sparseness
    noise = cpmtps.noise
load_settings()

prediction_border = patch_radius


def setup_arrays():
    global shared_input_data_base, shared_output_data_base, shared_prediction_base, shared_weights_base, shared_states_base, shared_w_base
    global shared_input_data, shared_output_data, shared_prediction, shared_weights, shared_states, shared_w

    ###print("setting up arrays...")
    shared_input_data_base = multiprocessing.Array(ctypes.c_double, ndata*N*N)
    shared_input_data = np.ctypeslib.as_array(shared_input_data_base.get_obj())
    shared_input_data = shared_input_data.reshape(-1, N, N)

    shared_output_data_base = multiprocessing.Array(ctypes.c_double, ndata*N*N)
    shared_output_data = np.ctypeslib.as_array(shared_output_data_base.get_obj())
    shared_output_data = shared_output_data.reshape(-1, N, N)

    shared_prediction_base = multiprocessing.Array(ctypes.c_double, predictionLength*N*N)
    shared_prediction = np.ctypeslib.as_array(shared_prediction_base.get_obj())
    shared_prediction = shared_prediction.reshape(-1, N, N)

    shared_weights_base = multiprocessing.Array(ctypes.c_double, (N-2*prediction_border)*(N-2*prediction_border)*(eff_sigma*eff_sigma + 1 + n_units))
    shared_weights = np.ctypeslib.as_array(shared_weights_base.get_obj())
    shared_weights = shared_weights.reshape(N-2*prediction_border, N-2*prediction_border, 1, eff_sigma*eff_sigma + 1 + n_units)

    shared_states_base = multiprocessing.Array(ctypes.c_double, (N-2*prediction_border)*(N-2*prediction_border)*(n_units))
    shared_states = np.ctypeslib.as_array(shared_states_base.get_obj())
    shared_states = shared_states.reshape(N-2*prediction_border, N-2*prediction_border, n_units, 1)

    shared_w_base = multiprocessing.Array(ctypes.c_double, n_units*n_units)
    shared_w = np.ctypeslib.as_array(shared_w_base.get_obj())
    shared_w = shared_w.reshape(n_units, n_units)

    ###print("setting up finished")
setup_arrays()

def generate_data(N, Ngrid):
    data = None

    if direction in ["uv", "vu"]:
        if not os.path.exists("../../cache/barkley/raw/{0}_{1}.uv.dat.npy".format(N, Ngrid)):
            data = bh.generate_uv_data(N, 50000, 5, Ngrid=Ngrid)
            np.save("../../cache/barkley/raw/{0}_{1}.uv.dat.npy".format(N, Ngrid), data)
        else:
            data = np.load("../../cache/barkley/raw/{0}_{1}.uv.dat.npy".format(N, Ngrid))
    elif direction.startswith("bocf_"):
        if not os.path.exists("../../cache/bocf/raw/{0}_{1}.uvws.dat.npy".format(N, Ngrid)):
            print("NO BOCF data set found. Please generate a chaotic data set manually.")
        else:
            data = np.load("../../cache/bocf/raw/{0}_{1}.uvws.dat.npy".format(N, Ngrid))
    else:
        if not os.path.exists("../../cache/mitchell/raw/{0}_{1}.vh.dat.npy".format(N, Ngrid)):
            data = mh.generate_vh_data(N, 20000, 50, Ngrid=Ngrid)
            np.save("../../cache/mitchell/raw/{0}_{1}.vh.dat.npy".format(N, Ngrid), data)
        else:
            data = np.load("../../cache/mitchell/raw/{0}_{1}.vh.dat.npy".format(N, Ngrid))

    #at the moment we are doing a u -> v / v -> h cross prediction (index 0 -> index 1)
    if (direction in ["vu", "hv"]):
        #switch the entries for the v -> u / h -> v prediction
        tmp = data[0].copy()
        data[0] = data[1].copy()
        data[1] = tmp.copy()

    if direction.startswith("bocf_"):
        real_data = np.empty((2, N, Ngrid, Ngrid))

        if direction[5] == "u":
            real_data[0] = data[0].copy()
        elif direction[5] == "v":
            real_data[0] = data[1].copy()
        elif direction[5] == "w":
            real_data[0] = data[2].copy()
        elif direction[5] == "s":
            real_data[0] = data[3].copy()

        if direction[6] == "u":
            real_data[1] = data[0].copy()
        elif direction[6] == "v":
            real_data[1] = data[1].copy()
        elif direction[6] == "w":
            real_data[1] = data[2].copy()
        elif direction[6] == "s":
            real_data[1] = data[3].copy()

        data = real_data

    global means_train
    means_train = [0, 0] #np.mean(data[:trainLength], axis=(1, 2))
    data[0] -= means_train[0]
    data[1] -= means_train[1]

    if cpmtps.use_noise:
        data[0] += np.random.normal(loc=0.0, scale=noise, size=data[0].shape)

    if not direction.startswith("bocf"):
        data[0][data[0] < 0.0] = 0.0
        data[0][data[0] > 1.0] = 1.0

    shared_input_data[:ndata] = data[0]
    shared_output_data[:ndata] = data[1]

def prepare_predicter(y, x, training_data_in, training_data_out):
    global w_stored
    if prediction_mode == "ESN":
        isInner = False
        if y < patch_radius or y >= N-patch_radius or x < patch_radius or x >= N-patch_radius:
            #frame
            min_border_distance = np.min([y, x, N-1-y, N-1-x])
            input_dimension = int((2*min_border_distance+1)**2)
        else:
            #inner
            isInner = True
            input_dimension = eff_sigma*eff_sigma

        input_scaling = None
        if useInputScaling:
            #approximate the input scaling using the MI
            input_scaling = calculate_esn_mi_input_scaling(training_data_in, training_data_out[:, 0])

        predicter = ESN(n_input=input_dimension, n_output=1, n_reservoir=n_units,
                        weight_generation="advanced", leak_rate=leaking_rate, spectral_radius=spectral_radius,
                        random_seed=random_seed, noise_level=noise_level, sparseness=sparseness, input_scaling=input_scaling,
                        regression_parameters=[regression_parameter], solver="lsqr")
        if isInner:
            if (not bool(w_stored.value)):
                shared_w[:] = predicter._W[:]
                w_stored.value = True
                print("generated new weights...")
            else:
                predicter._W[:] = shared_w[:]
                #print("used old weights...")

    elif prediction_mode == "NN":
        predicter = NN(k=k)
    elif prediction_mode == "RBF":
        predicter = RBF(sigma=width, basisPoints=basis_points)
    else:
        raise ValueError("No valid prediction_mode choosen! (Value is now: {0})".format(prediction_mode))

    return predicter

def prepare_fit_data(y, x, pr, skip, def_param=(shared_input_data, shared_output_data)):
    if (prediction_mode in ["NN", "RBF"]):
        delayed_patched_input_data = create_2d_delay_coordinates(shared_input_data[:, y-pr:y+pr+1, x-pr:x+pr+1][:, ::skip, ::skip], ddim, tau=tau[direction])
        delayed_patched_input_data = delayed_patched_input_data.reshape(ndata, -1)

        delayed_patched_input_data_train = delayed_patched_input_data[:trainLength]

        training_data_in = delayed_patched_input_data_train.reshape(trainLength, -1)
        training_data_out = shared_output_data[:trainLength, y, x].reshape(-1,1)

    else:
        training_data_in = shared_input_data[:trainLength][:, y - pr:y + pr+1, x - pr:x + pr+1][:, ::skip, ::skip].reshape(trainLength, -1)
        training_data_out = shared_output_data[:trainLength][:, y, x].reshape(-1, 1)

    return training_data_in, training_data_out

def prepare_predict_data(y, x, pr, skip, def_param=(shared_input_data, shared_output_data)):
    if (prediction_mode in ["NN", "RBF"]):
        delayed_patched_input_data = create_2d_delay_coordinates(shared_input_data[:, y-pr:y+pr+1, x-pr:x+pr+1][:, ::skip, ::skip], ddim, tau=tau[direction])
        delayed_patched_input_data = delayed_patched_input_data.reshape(ndata, -1)

        delayed_patched_input_data_test = delayed_patched_input_data[trainLength:trainLength+predictionLength]

        test_data_in = delayed_patched_input_data_test.reshape(predictionLength, -1)
        test_data_out = shared_output_data[trainLength:trainLength+predictionLength, y, x].reshape(-1,1)

    else:
        test_data_in = shared_input_data[trainLength:trainLength+predictionLength][:, y - pr:y + pr+1, x - pr:x + pr+1][:, ::skip, ::skip].reshape(predictionLength, -1)
        test_data_out = shared_output_data[trainLength:trainLength+predictionLength][:, y, x].reshape(-1, 1)

    return test_data_in, test_data_out

def fit_predict_frame_pixel(y, x, def_param=(shared_input_data, shared_output_data)):
    min_border_distance = np.min([y, x, N-1-y, N-1-x])
    training_data_in, training_data_out, = prepare_fit_data(y, x, min_border_distance, 1)
    test_data_in, _ = prepare_predict_data(y, x, min_border_distance, 1)

    predicter = prepare_predicter(y, x, training_data_in, training_data_out)
    predicter.fit(training_data_in, training_data_out)
    pred = predicter.predict(test_data_in)
    pred = pred.ravel()

    return pred, predicter

def predict_inner_pixel(y, x, average_weight, def_param=(shared_input_data, shared_output_data)):
    test_data_in, _ = prepare_predict_data(y, x, patch_radius, sigma_skip)

    predicter = prepare_predicter(y, x, test_data_in, None)
    try:
        predicter._W_out = average_weight
        predicter._x = shared_states[y-prediction_border, x-prediction_border]

        pred = predicter.predict(test_data_in)
        pred = pred.ravel()

    except LinAlgError:
        print("(y,x) = ({0},{1}) raised a SVD error".format(y, x))

        pred = np.zeros(predictionLength)

    return pred, predicter

def fit_inner_pixel(y, x, def_param=(shared_input_data, shared_output_data)):
    training_data_in, training_data_out = prepare_fit_data(y, x, patch_radius, sigma_skip)

    predicter = prepare_predicter(y, x, training_data_in, training_data_out)
    try:
        predicter.fit(training_data_in, training_data_out)

    except LinAlgError:
        print("(y,x) = ({0},{1}) raised a SVD error".format(y, x))

    return predicter

def process_prediction_thread_results(q, nb_results, def_param=(shared_prediction, shared_output_data, shared_weights, shared_states)):
    global prediction

    bar = progressbar.ProgressBar(max_value=nb_results, redirect_stdout=True, poll_interval=0.0001)
    bar.update(0)

    finished_results = 0

    while True:
        if (finished_results == nb_results):
            bar.finish()
            return

        new_data = q.get()
        finished_results += 1

        ind_y, ind_x, data = new_data

        shared_prediction[:, ind_y, ind_x] = data

        bar.update(finished_results)

def process_fit_inner_thread_results(q, nb_results, def_param=(shared_prediction, shared_output_data)):
    global prediction, shared_weights

    bar = progressbar.ProgressBar(max_value=nb_results, redirect_stdout=True, poll_interval=0.0001)
    bar.update(0)

    finished_results = 0

    while True:
        if (finished_results == nb_results):
            bar.finish()
            return

        new_data = q.get()
        finished_results += 1

        #ind_y, ind_x, data = new_data
        ind_y, ind_x, state, weights = new_data

        shared_weights[ind_y-prediction_border, ind_x-prediction_border] = weights
        shared_states[ind_y-prediction_border, ind_x-prediction_border] = state

        bar.update(finished_results)

def fit_inner_predicter_init(q):
    fit_inner_predicter.q = q
def fit_inner_predicter(data):
    y, x = data

    predicter = fit_inner_pixel(y, x)
    fit_inner_predicter.q.put((y, x, predicter._x, predicter._W_out.flatten()))

def get_prediction_init(q, average_weight):
    get_prediction.q = q
    get_prediction.average_weight = average_weight
def get_prediction(data):
    y, x = data

    pred = None
    if y < patch_radius or y >= N-patch_radius or x < patch_radius or x >= N-patch_radius:
        #frame
        pred, predicter = fit_predict_frame_pixel(y, x)
    else:
        #inner
        pred, predicter = predict_inner_pixel(y, x, get_prediction.average_weight)
    get_prediction.q.put((y, x, pred))

def mainFunction():
    global shared_prediction, shared_weights, w_stored

    w_stored = Value("i", False)

    if trainLength + predictionLength > ndata:
        print("Please adjust the trainig and testing phase length!")
        exit()

    generate_data(ndata, Ngrid=N)

    jobs = []
    for y in range(prediction_border, N-prediction_border):
        for x in range(prediction_border, N-prediction_border):
            jobs.append((y, x))

    print("fitting...")
    #fit the reservoirs
    queue = Queue() # use manager.queue() ?
    pool = Pool(processes=16, initializer=fit_inner_predicter_init, initargs=[queue,])

    process_results_process = Process(target=process_fit_inner_thread_results, args=(queue, len(jobs)))
    process_results_process.start()
    pool.map(fit_inner_predicter, jobs)
    pool.close()

    print("calculate the average weight")
    #calculate the average weight
    print(shared_weights.shape)
    average_weight = np.average(shared_weights, axis=(0, 1))


    jobs = []
    for y in range(N):
        for x in range(N):
            jobs.append((y, x))

    queue = Queue() # use manager.queue() ?
    pool = Pool(processes=16, initializer=get_prediction_init, initargs=[queue, average_weight])

    process_results_process = Process(target=process_prediction_thread_results, args=(queue, len(jobs)))
    process_results_process.start()
    pool.map(get_prediction, jobs)
    pool.close()

    #predict now with w_out=<w_out>
    process_results_process.join()

    #output data
    shared_prediction = shared_prediction + means_train[1]

    if not direction.startswith("bocf"):
        shared_prediction[shared_prediction < 0.0] = 0.0
        shared_prediction[shared_prediction > 1.0] = 1.0

    diff = (shared_output_data[trainLength:trainLength+predictionLength]-shared_prediction)
    mse_validation = np.mean((diff[:predictionLength-testLength])**2)
    mse_test = np.mean((diff[predictionLength-testLength:predictionLength])**2)
    print("validation error: {0}".format(mse_validation))
    print("test error: {0}".format(mse_test))
    print("inner test error: {0}".format(np.mean((diff[predictionLength-testLength:predictionLength, patch_radius:N-patch_radius, patch_radius:N-patch_radius])**2)))

    view_data = [("Orig", shared_output_data[trainLength:]), ("Pred", shared_prediction), ("Source", shared_input_data[trainLength:]), ("Diff", diff)]

    model = "mitchell"
    if direction in ["uv", "vu"]:
        model = "barkley"
    elif direction.startswith("bocf_"):
        model = "bocf"

    if cpmtps.use_noise:
        if prediction_mode == "NN":
            output_file = open("../../cache/{0}/viewdata/{1}/{2}_viewdata_avg_noise_{3}_{4}_{5}_{6}_{7}_{8}.dat".format(
                model, direction, prediction_mode.lower(), trainLength, sigma, sigma_skip, ddim, k, noise), "wb")
        elif prediction_mode == "RBF":
            output_file = open("../../cache/{0}/viewdata/{1}/{2}_viewdata_avg_noise_{3}_{4}_{5}_{6}_{7}_{8}_{9}.dat".format(
                model, direction, prediction_mode.lower(), trainLength, sigma, sigma_skip, ddim, width, basis_points, noise), "wb")
        else:
            output_file = open("../../cache/{0}/viewdata/{1}/{2}_viewdata_avg_noise_{3}_{4}_{5}_{6}_{7}_{8}.dat".format(
                model, direction, prediction_mode.lower(), trainLength, sigma, sigma_skip, regression_parameter, n_units, noise), "wb")

    else:
        if prediction_mode == "NN":
            output_file = open("../../cache/{0}/viewdata/{1}/{2}_viewdata_avg_no_noise_{3}_{4}_{5}_{6}_{7}_{8}.dat".format(
                model, direction, prediction_mode.lower(), trainLength, sigma, sigma_skip, ddim, k, noise), "wb")
        elif prediction_mode == "RBF":
            output_file = open("../../cache/{0}/viewdata/{1}/{2}_viewdata_avg_no_noise_{3}_{4}_{5}_{6}_{7}_{8}_{9}.dat".format(
                model, direction, prediction_mode.lower(), trainLength, sigma, sigma_skip, ddim, width, basis_points, noise), "wb")
        else:
            output_file = open("../../cache/{0}/viewdata/{1}/{2}_viewdata_avg_no_noise_{3}_{4}_{5}_{6}_{7}_{8}.dat".format(
                model, direction, prediction_mode.lower(), trainLength, sigma, sigma_skip, regression_parameter, n_units, noise), "wb")
    pickle.dump(view_data, output_file)
    output_file.close()

    print("done")

if __name__== '__main__':
    mainFunction()
