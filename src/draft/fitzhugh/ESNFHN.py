import numpy as np
import numpy.random as rnd
from BaseESNFHN import BaseESNFHN

from sklearn.linear_model import Ridge
from sklearn.svm import SVR
from sklearn.linear_model import LogisticRegression


class ESNFHN(BaseESNFHN):
    def __init__(self, n_input, n_reservoir, n_output,
                spectral_radius=1.0, noise_level=0.01, input_scaling=None,
                a=-0.7 , b=0.8, deltaT=1e-1, r=1.0/0.08,
                sparseness=0.2, random_seed=None,
                out_activation=lambda x:x, out_inverse_activation=lambda x:x,
                weight_generation='naive', bias=1.0, output_bias=1.0,
                output_input_scaling=1.0, solver='pinv', regression_parameters={}):

        super(ESNFHN, self).__init__(n_input, n_reservoir, n_output, spectral_radius, noise_level, input_scaling, a, b, deltaT, r, sparseness, random_seed, out_activation,
                out_inverse_activation, weight_generation, bias, output_bias, output_input_scaling)


        self._solver = solver
        self._regression_parameters = regression_parameters
        """
        allowed values for the solver:
            pinv
            lsqr

            sklearn_auto
            sklearn_svd
            sklearn_cholesky
            sklearn_lsqr
            sklearn_sag
        """

    def fit(self, inputData, outputData, transient_quota=0.05):
        if (inputData.shape[0] != outputData.shape[0]):
            raise ValueError("Amount of input and output datasets is not equal - {0} != {1}".format(inputData.shape[0], outputData.shape[0]))

        trainLength = inputData.shape[0]

        skipLength = int(trainLength*transient_quota)

        #define states' matrix
        self._X = np.zeros((1+self.n_input+self.n_reservoir,trainLength-skipLength))

        self._v = np.zeros((self.n_reservoir,1))
        self._w = np.zeros((self.n_reservoir,1))

        for t in range(trainLength):
            u = super(ESNFHN, self).update(inputData[t])
            if (t >= skipLength):
                #add valueset to the states' matrix
                self._X[:,t-skipLength] = np.vstack((self.output_bias, self.output_input_scaling*u, self._v))[:,0]

        #define the target values
        #                                  +1
        Y_target = self.out_inverse_activation(outputData).T[:,skipLength:]

        #W_out = Y_target.dot(X.T).dot(np.linalg.inv(X.dot(X.T) + regressionParameter*np.identity(1+reservoirInputCount+reservoirSize)) )


        if (self._solver == "pinv"):
            """print("pinv")
            import pycuda.autoinit
            import pycuda.driver as drv
            import pycuda.gpuarray as gpuarray
            import skcuda.linalg as culinalg
            import skcuda.misc as cumisc
            culinalg.init()

            X_gpu = gpuarray.to_gpu(self._X)
            X_inv_gpu = culinalg.pinv(X_gpu)
            Y_gpu = gpuarray.to_gpu(Y_target)
            W_out_gpu = Y_gpu * W_out_gpu
            pred_gpu = W_out_gpu * X_gpu

            self._W_out = gpuarray.from_gpu(W_out_gpu)
            """
            self._W_out = np.dot(Y_target, np.linalg.pinv(self._X))

            #calculate the training error now
            train_prediction = self.out_activation((np.dot(self._W_out, self._X)).T)

        elif (self._solver == "lsqr"):
            self._W_out = np.dot(np.dot(Y_target, self._X.T),np.linalg.inv(np.dot(self._X,self._X.T) + self._regression_parameters[0]*np.identity(1+self.n_input+self.n_reservoir)))

            #calculate the training error now
            train_prediction = self.out_activation(np.dot(self._W_out, self._X).T)

        elif (self._solver in ["sklearn_auto", "sklearn_lsqr", "sklearn_sag", "sklearn_svd"]):
            mode = self._solver[8:]
            self._ridgeSolver = Ridge(**self._regression_parameters, solver=mode)

            self._ridgeSolver.fit(self._X.T, Y_target.T)
            train_prediction = self.out_activation(self._ridgeSolver.predict(self._X.T))

        elif (self._solver in ["sklearn_svr", "sklearn_svc"]):
            self._ridgeSolver = SVR(**self._regression_parameters)

            self._ridgeSolver.fit(self._X.T, Y_target.T.ravel())
            train_prediction = self.out_activation(self._ridgeSolver.predict(self._X.T))

        """
        #alternative represantation of the equation

        Xt = self._X.T

        A = np.dot(self._X, Y_target.T)

        B = np.linalg.inv(np.dot(self._X, Xt)  + regression_parameter*np.identity(1+self.n_input+self.n_reservoir))

        self._W_out = np.dot(B, A)
        self._W_out = self._W_out.T
        """

        training_error = np.sqrt(np.mean((train_prediction - outputData[skipLength:])**2))
        return training_error

    def generate(self, n, initial_input, continuation=True, initial_data=None, update_processor=lambda x:x):
        if (self.n_input != self.n_output):
            raise ValueError("n_input does not equal n_output. The generation mode uses its own output as its input. Therefore, n_input has to be equal to n_output - please adjust these numbers!")

        if (not continuation):
            self._v = np.zeros(self._v.shape)

            if (initial_data is not None):
                for t in range(initial_data.shape[0]):
                    #TODO Fix
                    super(ESNFHN, self).update(initial_data[t])

        predLength = n

        Y = np.zeros((self.n_output,predLength))
        inputData = initial_input
        for t in range(predLength):
            u = super(ESNFHN, self).update(inputData)

            if (self._solver in ["sklearn_auto", "sklearn_lsqr", "sklearn_sag", "sklearn_svd"]):
                y = self._ridgeSolver.predict(np.vstack((self.output_bias, self.output_input_scaling*u, self._v)).T)
            else:
                y = np.dot(self._W_out, np.vstack((self.output_bias, self.output_input_scaling*u, self._v)))

            #y = np.dot(self._W_out, np.vstack((self.output_bias, self.output_input_scaling*u, self._x)))
            y = self.out_activation(y[:,0])
            Y[:,t] = update_processor(y)
            inputData = y

        return Y.T

    def predict(self, inputData, continuation=True, initial_data=None, update_processor=lambda x:x):
        if (not continuation):
            self._v = np.zeros(self._v.shape)

            if (initial_data is not None):
                for t in range(initial_data.shape[0]):
                    super(ESNFHN, self).update(initial_data[t])

        predLength = inputData.shape[0]

        Y = np.zeros((self.n_output,predLength))

        for t in range(predLength):
            u = super(ESNFHN, self).update(inputData[t])

            if (self._solver in ["sklearn_auto", "sklearn_lsqr", "sklearn_sag", "sklearn_svd", "sklearn_svr"]):
                y = self._ridgeSolver.predict(np.vstack((self.output_bias, self.output_input_scaling*u, self._v)).T).reshape((-1,1))
            else:
                y = np.dot(self._W_out, np.vstack((self.output_bias, self.output_input_scaling*u, self._v)))

            Y[:,t] = update_processor(self.out_activation(y[:,0]))

        return Y.T
