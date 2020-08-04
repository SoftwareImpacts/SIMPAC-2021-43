import unittest
import numpy as np
import torch
import math
import scipy.stats

from gpytorch.distributions import MultivariateNormal
from bnn_priors.mcmc import VerletSGLD
from bnn_priors.models import DenseNet
from bnn_priors import prior

from .utils import requires_float64
from .test_sgld import GaussianModel

def store_verlet_state(sgld):
    return zip(*((p.detach().clone(), state['momentum_buffer'].detach().clone())
                 for p, state in sgld.state.items()))

def zip_allclose(sequence_a, sequence_b):
    return (torch.allclose(a, b) for a, b in zip(sequence_a, sequence_b))

def new_model_loss(N=10, D=1):
    x = torch.randn(N, 1)
    y = x.sin()
    model = DenseNet(x.size(-1), y.size(-1), 10, noise_std=0.1)
    def loss():
        model.zero_grad()
        v = model.potential_avg(x, y, eff_num_data=1.)
        v.backward()
        return v
    return model, loss


class VerletSGLDTest(unittest.TestCase):
    """
    Q: Why don't we have this kind of test for the VerletSGLD?

    A: because, VerletSGLD is not, and should not be, reversible. If it were
    exactly reversible, the expression for the acceptance probability would be
    the same as for HMC. However, Langevin dynamics do not preserve the volume.
    For example, they shrink the momentum by sqrt(a) at every step, and compensate
    with injected noise.

    Thus the name of the class is a little improper, it's not really a Verlet
    integrator.
    """

    @requires_float64
    def test_distribution_preservation(self, n_vars=50, n_dim=1000, n_samples=200, mh_freq=4, do_rejection=True, seed=145):
        """Tests whether VerletSGLD preserves the distribution of a  Gaussian potential correctly.

        Q: Why is the learning rate different compared to the SGLD test?
        A: VerletSGLD should be able to handle a larger learning rate because
           it uses a more accurate integration scheme, and also corrects errors
           with the M-H step.
        """
        torch.manual_seed(seed)
        mean, std = 1., 2.
        temperature = 3/4
        model = GaussianModel(N=n_vars, D=n_dim, mean=mean, std=std)
        # `num_data=1` to prevent scaling the Gaussian potential
        sgld = VerletSGLD(prior.params_with_prior(model), lr=1/32, num_data=1,
                          momentum=0.9, temperature=temperature)
        model.sample_all_priors()
        with torch.no_grad():
            for p in prior.params_with_prior(model):
                p.sub_(mean).mul_(temperature**.5).add_(mean)

        # Set the preconditioner randomly
        for _, state in sgld.state.items():
            state['preconditioner'] = (torch.rand(()).item() + 0.2) / math.sqrt(4)

        sgld.sample_momentum()

        sum_acceptance = 0.
        n_acceptance = 0
        assert n_samples % mh_freq == 0
        for step in range(n_samples+1):
            if step % mh_freq == 0:
                if step != 0:
                    loss = sgld.final_step(model.potential_avg_closure).item()
                    delta_energy = sgld.delta_energy(prev_loss, loss)
                    if do_rejection:
                        rejected = sgld.maybe_reject(delta_energy)
                    else:
                        rejected = False
                    if rejected:
                        with torch.no_grad():
                            assert np.allclose(prev_loss, model.potential_avg().item())
                    #     print(f"Rejected sample, with P(accept)={math.exp(-delta_energy)}")
                    # else:
                    #     print(f"Accepted sample, with P(accept)={math.exp(-delta_energy)}")
                    n_acceptance += 1
                    sum_acceptance += min(1., math.exp(-delta_energy))

                    if step == n_samples:
                        break

                prev_loss = sgld.initial_step(model.potential_avg_closure, save_state=True).item()
            else:
                sgld.step(model.potential_avg_closure)

        assert sum_acceptance/n_acceptance > 0.6  # Was 0.73 at commit 56988f7

        parameters = np.empty(n_vars*n_dim)
        kinetic_temp = np.empty(n_vars)
        config_temp = np.empty(n_vars)
        for i, (p, state) in enumerate(sgld.state.items()):
            parameters[i*n_dim:(i+1)*n_dim] = p.detach().numpy()
            kinetic_temp[i] = state['est_temperature']
            config_temp[i] = state['est_config_temp']

        # Test whether the parameters are 1-d Gaussian distributed
        statistic, (critical_value, *_), (significance_level, *_
                                          ) = scipy.stats.anderson(parameters, dist='norm')

        assert significance_level == 15, "next line does not check for significance of 15%"
        assert statistic < critical_value, "the samples are not Normal with p<0.15"

        def norm(x): return scipy.stats.norm.cdf(x, loc=mean, scale=std*temperature**.5)
        _, pvalue = scipy.stats.ks_1samp(parameters, norm, mode='exact')
        assert pvalue >= 0.3, "the samples are not Normal with the correct variance with p<0.3"

        def chi2(x): return scipy.stats.chi2.cdf(x, df=n_dim, loc=0., scale=temperature/n_dim)
        _, pvalue = scipy.stats.ks_1samp(config_temp, chi2, mode='exact')
        assert pvalue >= 0.3, "the configurational temperature is not Chi^2 with p<0.3"
        _, pvalue = scipy.stats.ks_1samp(kinetic_temp, chi2, mode='exact')
        assert pvalue >= 0.3, "the kinetic temperature is not Chi^2 with p<0.3"


if __name__ == '__main__':
    """ There are 4 probabilistic assertions in the test in `verlet_sgld.py`.
    Assuming they are independent, the test should pass `(1-.15)*(1-.3)**3 =
    29.15%` of the time. Using this script that goes through random seeds 0-50,
    the test succeeds 34% of the time, 32% of the time if you omit the M-H
    correction. Probably good enough. """
    import tqdm
    test = VerletSGLDTest()
    norand_succ = 0
    norand_total = 0
    for seed in tqdm.trange(50):
        try:
            test.test_distribution_preservation(do_rejection=False, seed=seed)
            norand_succ += 1
        except AssertionError:
            pass
        norand_total += 1
    print("No rejection success %:", norand_succ/norand_total)

    rand_succ = 0
    rand_total = 0
    for seed in tqdm.trange(50):
        try:
            test.test_distribution_preservation(do_rejection=True, seed=seed)
            rand_succ += 1
        except AssertionError:
            pass
        rand_total += 1

    print("M-H rejection success %:", rand_succ/rand_total)
