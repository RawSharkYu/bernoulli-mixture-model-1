from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals
import numpy as np

from bernoullimix._bernoulli import bernoulli_prob_for_observations, maximise_emissions


class ConvergenceStatus(object):

    converged = None
    number_of_iterations = None
    likelihood_trace = None

    def __init__(self, converged, number_of_iterations, likelihood_trace=None):
        self.converged = converged
        self.number_of_iterations = number_of_iterations
        self.likelihood_trace = likelihood_trace

    @property
    def trace_available(self):
        return self.likelihood_trace is not None

    def __repr__(self):
        converged_text = 'converged' if self.converged else 'did not converge'
        trace_text = ' (trace available)' if self.trace_available else ''
        return '<{} in {:,} iterations{}>'.format(converged_text, self.number_of_iterations,
                                                  trace_text)


class BernoulliMixture(object):
    _number_of_components = None
    _number_of_dimensions = None

    _mixing_coefficients = None
    _emission_probabilities = None

    def __init__(self, number_of_components,
                 number_of_dimensions,
                 mixing_coefficients,
                 emission_probabilities):
        """
        Initialises a Bernoulli Mixture Model

        :param number_of_components: number of components in the model (i.e. K)
        :param number_of_dimensions: number of independent Bernoullis in the model (i.e. D)
        :param mixing_coefficients: K-dimensional array of the mixture components for the data
        :param emission_probabilities: (K, D)-dimensional matrix of the probabilities of emitting
                                       `True` in each bernoulli, given the component.
        """

        self._number_of_components = int(number_of_components)
        self._number_of_dimensions = int(number_of_dimensions)

        self._mixing_coefficients = np.asarray(mixing_coefficients, dtype=float)
        self._emission_probabilities = np.asarray(emission_probabilities, dtype=float)

        self._validate()

    def _bounded_between_zero_and_one(self, array_):
        return np.all((array_ >= 0) & (array_ <= 1))

    def _validate(self):
        K = self.number_of_components
        D = self.number_of_dimensions

        if not self.mixing_coefficients.shape == (K, ):
            raise ValueError('Wrong shape of mixing coefficients provided. '
                             'Expected {}, got {}'.format((K,), self.mixing_coefficients))

        if not np.isclose(np.sum(self.mixing_coefficients), 1.0):
            raise ValueError('Mixing coefficient probabilities do not sum to one. Got: {}'.format(
                np.sum(self.mixing_coefficients)))

        if not self._bounded_between_zero_and_one(self.mixing_coefficients):
            raise ValueError('Mixing coefficients not bounded between 0 and 1')

        if not self.emission_probabilities.shape == (K, D):
            raise ValueError('Wrong shape of emission probabilities matrix. '
                             'Expected {}, got {}'.format((K, D), self.emission_probabilities.shape))

        if not self._bounded_between_zero_and_one(self.emission_probabilities):
            raise ValueError('Emission probabilities not bounded between 0 and 1')

    @property
    def number_of_components(self):
        return self._number_of_components

    @property
    def number_of_dimensions(self):
        return self._number_of_dimensions

    @property
    def emission_probabilities(self):
        return self._emission_probabilities

    @property
    def mixing_coefficients(self):
        return self._mixing_coefficients

    @property
    def number_of_free_parameters(self):
        """
        Returns number of free parameters for module
        :return:
        """
        # K - 1 params for mixture components
        # (K * D) parameters for emission probabilities

        return (self.number_of_components - 1) + \
               (self.number_of_components * self.number_of_dimensions)

    @classmethod
    def _aggregate_dataset(cls, dataset):
        """
        Take the dataset and return only its unique rows, along with their counts

        :param dataset: dataset to process
        :return: tuple. First element is the unique rows in data,
                        second element is the number of times they occur.
        """
        # based on http://stackoverflow.com/a/16971224/171400

        rows, columns = dataset.shape

        structured = dataset.view(dataset.dtype.descr * columns)
        unique, counts = np.unique(structured, return_counts=True)

        unique = unique.view(dataset.dtype).reshape(-1, columns)
        return unique, counts

    def _penalised_likelihood(self, log_likelihood, psi):
        """
        Returns penalised likelihood computed as:

        $$
            -2L + \psi \eta
        $$
        Where $L$ is the log likelihood (provided),
        $\eta$ is the number of free parameters in the model,
        and $\psi$ is the provided penalty term.
        For instance set psi=2 to get AIC, or psi=log N to get BIC.

        :param log_likelihood: log likelihood
        :param psi: penalty term
        :return: penalised likelihood
        """
        return -2.0 * log_likelihood + psi * self.number_of_free_parameters

    def BIC(self, dataset):
        """
        Computes Bayesian Information Criterion
        :param dataset: dataset to compute BIC for
        :return: BIC
        """
        log_likelihood = self.log_likelihood(dataset)
        psi = np.log(len(dataset))
        return self._penalised_likelihood(log_likelihood, psi=psi)

    def AIC(self, dataset):
        """
        Computes Akaike Information Criterion
        :param dataset: dataset to compute AIC for
        :return: AIC
        """
        log_likelihood = self.log_likelihood(dataset)
        psi = 2
        return self._penalised_likelihood(log_likelihood, psi=psi)

    def sample(self, size, random_state=None):
        """
        Sample a `size` amount of observations from mixture model.

        :param size: the number of observations to sample
        :param random_state: (optional) random state to use.
        :return: (observations, true_components) -- two arrays. The generated observations and their
                true components.
        """
        random = np.random.RandomState(random_state)

        true_components = np.argmax(random.multinomial(1, self.mixing_coefficients, size=size),
                                    axis=1)
        observations = np.empty((size, self.number_of_dimensions))

        for component in range(self.number_of_components):
            mask = true_components == component
            n_samples_for_component = np.sum(true_components == component)

            for dimension in range(self.number_of_dimensions):
                prob = self.emission_probabilities[component, dimension]

                samples_for_component = random.binomial(1,
                                                        prob,
                                                        size=n_samples_for_component)

                observations[mask, dimension] = samples_for_component

        return observations, true_components

    def log_likelihood(self, dataset):

        if dataset.shape[1] != self.number_of_dimensions:
            raise ValueError('The dataset shape does not match number of dimensions.'
                             'Got {}, expected {}'.format(dataset.shape[1],
                                                          self.number_of_dimensions))

        unique_dataset, weights = self._aggregate_dataset(dataset)
        support = self._observation_emission_support(unique_dataset)
        return self._log_likelihood_from_support(support, weights)

    def _log_likelihood_from_support(self, support, weights):
        """
        Computes log likelihood from the support

        :param support: support (computed by `BernoulliMixture._observation_emission_support`)
        :param weights: weights for each support row (i.e. how many rows does it represent)
        :return:
        """
        return np.sum(np.log(np.sum(support, axis=1)) * weights)

    def _observation_emission_support(self, observations):
        """
        Returns point emission probabilities for a set of observations provided as array.
        Usually the code would just compute it for unique observations, and then
        weigh the support appropriately in calculations.
        :param observations: array of observations
        """

        observations = np.asarray(observations, dtype=bool)

        answer = np.empty((len(observations), self.number_of_components))

        for component in range(self.number_of_components):
            component_emission_probs = self.emission_probabilities[component]

            answer[:, component] = self.mixing_coefficients[component] * \
                                   bernoulli_prob_for_observations(component_emission_probs,
                                                                   observations)

        return answer

    @classmethod
    def _posterior_probability_of_class_given_support(cls, support):
        return (support.T / np.sum(support, axis=1)).T


    @classmethod
    def _m_step(cls, unique_zstar, unique_dataset, weights):

        u = np.sum(unique_zstar.T * weights, axis=1)
        sum_of_weights = np.sum(weights)

        N, K = unique_zstar.shape

        vs = maximise_emissions(unique_dataset, unique_zstar, weights)

        for k in range(K):
            vs[k] /= u[k]

        return u / sum_of_weights, vs

    def fit(self, dataset, iteration_limit=1000, convergence_threshold=1e-6,
            trace_likelihood=False):
        """
        Fits the mixture model to the dataset using EM algorithm.

        :param dataset: dataset to fit to
        :param iteration_limit: number of iterations to search. If none, will run till convergence
        :param convergence_threshold: threshold (for log likelihood) that marks convergence
        :param trace_likelihood: if set to true, the likelihood trace from optimisation
                                 will be returned
        :return: (float, `ConvergenceStatus`) : log likelihood of the dataset post fitting,
            and the information about convergence of the algorithm
        """

        dataset = np.asarray(dataset, dtype=bool)

        if dataset.shape[1] != self.number_of_dimensions:
            raise ValueError('The dataset shape does not match number of dimensions.'
                             'Got {}, expected {}'.format(dataset.shape[1],
                                                          self.number_of_dimensions))

        # Get only unique rows and their counts
        unique_dataset, counts = self._aggregate_dataset(dataset)

        iterations_done = 0

        previous_log_likelihood, current_log_likelihood = None, None

        if trace_likelihood:
            if iteration_limit:
                likelihood_trace = np.empty(iteration_limit, dtype=float)
            else:
                likelihood_trace = []
        else:
            likelihood_trace = None

        converged = False
        while iteration_limit is None or iterations_done < iteration_limit:
            unique_support = self._observation_emission_support(unique_dataset)

            current_log_likelihood = self._log_likelihood_from_support(unique_support, counts)
            if previous_log_likelihood is not None \
                and np.isclose(current_log_likelihood, previous_log_likelihood,
                               rtol=0, atol=convergence_threshold):
                converged = True
                break

            unique_z_star = self._posterior_probability_of_class_given_support(unique_support)

            pi, e = self._m_step(unique_z_star, unique_dataset, counts)

            self._mixing_coefficients = pi
            self._emission_probabilities = e

            if trace_likelihood:
                if iteration_limit:
                    likelihood_trace[iterations_done] = current_log_likelihood
                else:
                    likelihood_trace.append(current_log_likelihood)

            previous_log_likelihood = current_log_likelihood

            iterations_done += 1

        if trace_likelihood:
            if not iteration_limit:
                likelihood_trace = np.array(likelihood_trace)
            elif iterations_done < len(likelihood_trace):
                likelihood_trace = likelihood_trace[:iterations_done]

        convergence_status = ConvergenceStatus(converged, iterations_done, likelihood_trace)

        return current_log_likelihood, convergence_status

    def soft_assignment(self, dataset):
        """
        Returns soft assignment of dataset to classes given the model.

        :param dataset: Dataset to assign
        :return: (N, K) matrix of probabilities of the n-th observation comming from component K
        """

        support = self._observation_emission_support(dataset)
        probs = self._posterior_probability_of_class_given_support(support)

        return probs

    def hard_assignment(self, dataset):
        """
        Returns hard assignment of dataset to classes given the model

        :param dataset: Dataset to assign
        :return: N-vector of the most-likely component to generate that vector
        """

        probs = self.soft_assignment(dataset)
        return np.argmax(probs, axis=1)