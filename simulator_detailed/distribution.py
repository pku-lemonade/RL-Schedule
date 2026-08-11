import numpy as np
from scipy import stats


# --- Distribution factory functions ---
def norm_pdf(mu: float, sigma: float):
    """Return a normal distribution object with mean `mu` and standard deviation `sigma`."""
    return stats.norm(loc=mu, scale=sigma)


def beta_pdf(alpha: float, beta_param: float):
    """Return a beta distribution object with parameters `alpha` and `beta_param`."""
    return stats.beta(a=alpha, b=beta_param)


def gamma_pdf(shape: float, scale: float):
    """Return a gamma distribution object with given `shape` and `scale`."""
    return stats.gamma(a=shape, scale=scale)


# --- Core distribution class ---
class CoreDist:
    """Represents a core's processing time distribution and fail-slow behavior."""

    def __init__(self, mu: float = 1024, sigma: float = 62.25):
        self.mu = mu
        self.sigma = sigma
        # Normal distribution representing typical processing time
        self.normal_dist = norm_pdf(mu=mu, sigma=sigma / 4)
        # Normal distribution representing abnormal (fail-slow) behavior
        self.abnormal_dist = norm_pdf(mu=mu, sigma=sigma * 2)

    def range_cdf(self, a: float, b: float) -> float:
        """Return the probability that a sample falls between `a` and `b`."""
        return self.normal_dist.cdf(b) - self.normal_dist.cdf(a)

    def failslow_prob(self, x: float) -> float:
        """
        Estimate the probability that a core exhibits fail-slow behavior 
        given an observed value `x`.
        """
        tx = self.mu - abs(x - self.mu)
        return 1 - 2 * self.abnormal_dist.cdf(tx)

    def generate(self, size: int = 1) -> float:
        """Generate random samples from the normal distribution (default returns a single value)."""
        return self.normal_dist.rvs(size=size)[0]


# --- NoC distribution class ---
class NoCDist:
    """Represents the distribution of network-on-chip (NoC) communication delays."""

    def __init__(self, shape: float = 8.0, rate: float = 0.5):
        self.shape = shape
        self.rate = rate
        # Gamma distribution: scale is inverse of rate
        self.dist = gamma_pdf(shape=shape, scale=1.0 / rate)

    def range_cdf(self, a: float, b: float) -> float:
        """Return the probability that a sample falls between `a` and `b`."""
        return self.dist.cdf(b) - self.dist.cdf(a)

    def failslow_prob(self, x: float) -> float:
        """Estimate the probability of fail-slow behavior given a sample `x`."""
        return 1 - self.dist.cdf(x)

    def generate(self, size: int = 1) -> float:
        """Generate random samples from the gamma distribution (default returns a single value)."""
        return self.dist.rvs(size=size)[0]
