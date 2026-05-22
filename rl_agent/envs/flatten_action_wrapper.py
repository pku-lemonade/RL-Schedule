import numpy as np
import gymnasium as gym


class FlattenActionWrapper(gym.ActionWrapper):
    def __init__(self, env: gym.Env):
        super().__init__(env)
        if not isinstance(env.action_space, gym.spaces.MultiDiscrete):
            raise TypeError("FlattenActionWrapper expects a MultiDiscrete action space")

        self._nvec = np.array(env.action_space.nvec, dtype=np.int64)
        self.action_space = gym.spaces.Discrete(int(np.prod(self._nvec)))

    def action(self, action):
        flat_action = int(action)
        unraveled = np.unravel_index(flat_action, tuple(self._nvec))
        return np.asarray(unraveled, dtype=np.int64)
