import numpy as np
import numpy.random as rnd
import pickle

class BaseESN:
    def __init__(self, n_input, n_reservoir,
                spectral_radius=1.0, noise_level=0.01, input_scaling=None,
                leak_rate=1.0, sparseness=0.2, random_seed=None,
                out_activation=lambda x:x, out_inverse_activation=lambda x:x,
                weight_generation='naive', bias=1.0, output_bias=1.0, output_input_scaling=1.0):

                self.n_input = n_input
                self.n_reservoir = n_reservoir

                self.spectral_radius = spectral_radius
                self.noise_level = noise_level
                self.sparseness = sparseness
                self.leak_rate = leak_rate

                if (input_scaling is None):
                    input_scaling = np.ones(n_input)
                if (np.isscalar(input_scaling)):
                    input_scaling = np.repeat(input_scaling, n_input)
                else:
                    if (len(input_scaling) != self.n_input):
                        raise ValueError("Dimension of input_scaling ({0}) does not match the input data dimension ({1})".format(len(input_scaling), n_input))

                self._input_scaling_matrix = np.diag(input_scaling)
                self._expanded_input_scaling_matrix = np.diag(np.append([1.0],input_scaling))

                self.out_activation = out_activation
                self.out_inverse_activation = out_inverse_activation

                if (random_seed is not None):
                    rnd.seed(random_seed)

                self.bias = bias
                self.output_bias = output_bias
                self.output_input_scaling = output_input_scaling
                self._create_reservoir(weight_generation)

    def _create_reservoir(self, weight_generation):
        if (weight_generation == 'naive'):
            #random weight matrix from -0.5 to 0.5
            self._W = rnd.rand(self.n_reservoir, self.n_reservoir) - 0.5

            #set sparseness% to zero
            mask = rnd.rand(self.n_reservoir, self.n_reservoir) > self.sparseness
            self._W[mask] = 0.0

            _W_eigenvalues = np.abs(np.linalg.eig(self._W)[0])
            self._W *= self.spectral_radius / np.max(_W_eigenvalues)

        elif (weight_generation == 'advanced'):
            #two create W we must follow some steps:
            #at first, create a W = |W|
            #make it sparse
            #then scale its spectral radius to rho(W) = 1 (according to Yildiz with x(n+1) = (1-a)*x(n)+a*f(...))
            #then change randomly the signs of the matrix

            #random weight matrix from 0 to 0.5
            self._W = rnd.rand(self.n_reservoir, self.n_reservoir) / 2

            #set sparseness% to zero
            mask = rnd.rand(self.n_reservoir, self.n_reservoir) > self.sparseness
            self._W[mask] = 0.0

            _W_eigenvalues = np.abs(np.linalg.eig(self._W)[0])
            self._W *= self.spectral_radius / np.max(_W_eigenvalues)

            #change random signs
            random_signs = np.power(-1, rnd.random_integers(self.n_reservoir, self.n_reservoir))
            self._W = np.multiply(self._W, random_signs)

        else:
            raise ValueError("The weight_generation property must be one of the following values: naive, advanced")

        #random weight matrix for the input from -0.5 to 0.5
        self._W_input = np.random.rand(self.n_reservoir, 1+self.n_input)-0.5
        self._W_input = self._W_input.dot(self._expanded_input_scaling_matrix)

    def update(self, inputData):
        """
        returns the UNSCALED but reshaped input of this step
        """
        u = inputData.reshape(self.n_input, 1)
        self._x = (1.0-self.leak_rate)*self._x + self.leak_rate*np.arctan(np.dot(self._W_input, np.vstack((self.bias, u))) + np.dot(self._W, self._x)) + (np.random.rand()-0.5)*self.noise_level

        return u

    def save(self, path):
        f = open(path, "wb")
        pickle.dump(self, f)
        f.close()

    def load(path):
        f = open(path, "rb")
        result = pickle.load(f)
        f.close()
        return result