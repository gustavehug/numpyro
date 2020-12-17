import argparse
import math
import time
from functools import partial
import pickle

import numpy as np

import jax.numpy as jnp
import jax.random as random
from jax.scipy.linalg import cho_factor, cho_solve, solve_triangular

import numpyro
import numpyro.distributions as dist
from numpyro.infer import MCMC, NUTS, HMC, HMCGibbs, init_to_value
from numpyro.util import enable_x64


BETA_COV = 0.2


def kernel(X, Z, var, length, noise, jitter=1.0e-6, include_noise=True):
    deltaXsq = jnp.power((X[:, None] - Z) / length, 2.0)
    k = var * jnp.exp(-0.5 * deltaXsq)
    if include_noise:
        k += (noise + jitter) * jnp.eye(X.shape[0])
    return k


def model(X, Y):
    N, P = X.shape

    #var = numpyro.sample("kernel_var", dist.LogNormal(0.0, 2.0))
    noise = numpyro.sample("kernel_noise", dist.LogNormal(0.0, 5.0))
    #length = numpyro.sample("kernel_length", dist.LogNormal(0.0, 2.0))
    a = numpyro.sample("coeff_a", dist.Normal(0.0, 0.1))
    b = numpyro.sample("coeff_b", dist.Normal(0.0, 0.1))
    c = numpyro.sample("coeff_c", dist.Normal(0.0, 0.1))
    d = numpyro.sample("coeff_d", dist.Normal(0.0, 0.1))

    sigma_phi = numpyro.sample("sigma_phi", dist.HalfCauchy(0.01))

    beta = numpyro.sample("beta", dist.Normal(jnp.zeros(P), math.sqrt(BETA_COV) * jnp.ones(P)))

    #mean_phi = numpyro.deterministic("mean_phi", jnp.sum(beta * X, axis=-1))
    mean_phi = jnp.sum(beta * X, axis=-1)

    phi = mean_phi + sigma_phi * numpyro.sample("phi", dist.Normal(0.0, jnp.ones(N)))

    #k = kernel(phi, phi, var, length, noise)
    #numpyro.sample("Y", dist.MultivariateNormal(loc=jnp.zeros(X.shape[0]), covariance_matrix=k), obs=Y)

    Y_mean = a + b * phi + c * jnp.square(phi) + d * jnp.power(phi, 3.0)
    numpyro.sample("Y", dist.Normal(Y_mean, noise), obs=Y)


def _gibbs_fn(X, rng_key, gibbs_sites, hmc_sites):
    N, P = X.shape

    sigma_phi, phi = hmc_sites['sigma_phi'], hmc_sites['phi']
    mean_phi = jnp.sum(gibbs_sites['beta'] * X, axis=-1)
    phi = mean_phi + sigma_phi * phi

    X_phi = jnp.sum(X * phi[:, None], axis=0)

    XX = np.matmul(np.transpose(X), X)

    sigma_sq = jnp.square(sigma_phi)
    covar_inv = XX / sigma_sq + jnp.eye(P) / BETA_COV

    L = cho_factor(covar_inv, lower=True)[0]
    L_inv = solve_triangular(L, jnp.eye(P), lower=True)
    loc = cho_solve((L, True), X_phi) / sigma_sq

    beta_proposal = dist.MultivariateNormal(loc=loc, scale_tril=L_inv).sample(rng_key)

    return {'beta': beta_proposal}


def run_inference(args, rng_key, X, Y):
    #if False:
    #    samples = pickle.load(open('samples.pkl', "rb" ))
    #    values = {}
    #    for k, v in samples.items():
            #if k in ['mean_phi']: continue
    #        if k != 'phi': continue
    #        values[k] = np.mean(v, axis=0)
    #        print(k, values[k].shape)

    #init_strategy = init_to_value(values=values)
    #init_strategy = init_to_value(values={"kernel_var": 0.5, "kernel_noise": 0.1, "phi_sigma": 0.01,
    #                                      "kernel_length": 2.0, "phi": Y / 0.01})

    if args.strategy == "gibbs":
        gibbs_fn = partial(_gibbs_fn, X)
        hmc_kernel = NUTS(model, max_tree_depth=7)#, init_strategy=init_strategy)
        kernel = HMCGibbs(hmc_kernel, gibbs_fn=gibbs_fn, gibbs_sites=['beta'])
        mcmc = MCMC(kernel, args.num_warmup, args.num_samples, progress_bar=True)
    else:
        hmc_kernel = NUTS(model, max_tree_depth=7)#, init_strategy=init_strategy)
        mcmc = MCMC(hmc_kernel, args.num_warmup, args.num_samples, progress_bar=True)

    start = time.time()
    mcmc.run(rng_key, X, Y)
    mcmc.print_summary(exclude_deterministic=False)
    print('\nMCMC elapsed time:', time.time() - start)

    return mcmc.get_samples()


# create artificial regression dataset
def get_data(N=50, P=30, sigma_obs=0.05):
    np.random.seed(0)

    X = np.random.randn(N * P).reshape((N, P))
    Y = np.power(0.4 * X[:, 0] + 0.2 * X[:, 1], 3.0)
    Y += sigma_obs * np.random.randn(N)
    Y -= jnp.mean(Y)
    Y /= jnp.std(Y)

    assert X.shape == (N, P)
    assert Y.shape == (N,)

    return X, Y


def main(args):
    X, Y = get_data(N=args.num_data, P=args.P)

    rng_key, rng_key_predict = random.split(random.PRNGKey(0))
    samples = run_inference(args, rng_key, X, Y)
    print(samples.keys())

    #with open('samples.pkl', "wb") as f:
    #    pickle.dump(samples, f)

    #mean_phi = np.mean(samples['mean_phi'], axis=0)

    #from scipy.stats import pearsonr

    #r = pearsonr(mean_phi, Y)
    #print("r",r)
    #print("mean_phi/Y", mean_phi/Y)



if __name__ == "__main__":
    assert numpyro.__version__.startswith('0.4.1')
    parser = argparse.ArgumentParser(description="non-linear horseshoe")
    parser.add_argument("-n", "--num-samples", default=10000, type=int)
    parser.add_argument("--num-warmup", default=10000, type=int)
    parser.add_argument("--num-chains", default=1, type=int)
    parser.add_argument("--num-data", default=81, type=int)
    parser.add_argument("--strategy", default="gibbs", type=str, choices=["nuts", "gibbs"])
    parser.add_argument("--P", default=8, type=int)
    parser.add_argument("--device", default='cpu', type=str, help='use "cpu" or "gpu".')
    args = parser.parse_args()

    numpyro.set_platform(args.device)
    numpyro.set_host_device_count(args.num_chains)
    enable_x64()

    main(args)