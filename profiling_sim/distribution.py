from scipy import stats


class NoCDist:
    """Gamma distribution for NoC link bandwidth variance."""

    def __init__(self, shape: float = 8.0, rate: float = 0.5,
                 deterministic: bool = False):
        self.shape = shape
        self.rate = rate
        self.deterministic = deterministic
        if not deterministic:
            self.dist = stats.gamma(a=shape, scale=1.0 / rate)

    def generate(self, size: int = 1) -> float:
        if self.deterministic:
            return self.shape / self.rate
        return self.dist.rvs(size=size)[0]
