from env.atari_lib import make_atari, wrap_deepmind
import tensorflow as tf
from tensorflow.python.keras import layers
from algos.dqn.core import mlp_dqn
import numpy as np
import random
import copy
from utils.logx import EpochLogger
import gym
import os

debug_mode = True

class ReplayBuffer:
    def __init__(self, size):
        self.storage = []
        self.maxsize = size
        self.next_idx = 0
        self.size = 0

    def add(self, s, a, s_, r, done):
        data = (s, a, s_, r, done)
        if self.size < self.maxsize:
            self.storage.append(data)

        else:
            self.storage[self.next_idx] = data

        self.next_idx = (self.next_idx + 1) % self.maxsize
        self.size = min(self.size+1, self.maxsize)

    def sample(self, batch_size=32):
        ids = np.random.randint(0, self.size, size=batch_size)
        # storage = np.array(self.storage)
        state = []
        action = []
        state_ = []
        reward = []
        done = []
        for i in ids:
            (s, a, s_, r, d) = self.storage[i]
            state.append(s)
            action.append(a)
            state_.append(s_)
            reward.append(r)
            done.append(d)

        return np.array(state), np.array(action), np.array(state_), np.array(reward), np.array(done)


def linearly_decaying_epsilon(decay_period, step, warmup_steps, epsilon):
    step_left = decay_period + warmup_steps - step
    ep = step_left/decay_period*(1-epsilon)
    ep = np.clip(ep, 0, 1-epsilon)

    return epsilon + ep


class Dqn:
    def __init__(self, env_name, train_step=200, evaluation_step=1000, max_ep_len=200, epsilon_train=0.1,
                epsilon_eval=0.01, batch_size=32, replay_size=1e6,
                epsilon_decay_period=100, warmup_steps=0, iteration=200, gamma=0.99,
                target_update_period=50, update_period=10, logger_kwargs=dict()):

        self.logger = EpochLogger(**logger_kwargs)
        self.logger.save_config(locals())

        self.env = gym.make(env_name)

        self.train_step = train_step
        self.evaluation_step = evaluation_step
        self.max_ep_len = max_ep_len
        self.epsilon_train = epsilon_train
        self.epsilon_eval = epsilon_eval
        self.batch_size = batch_size
        self.replay_size = replay_size
        self.epsilon_decay_period = epsilon_decay_period
        self.warmup_steps = warmup_steps
        self.iteration = iteration
        self.replay_buffer = ReplayBuffer(replay_size)
        self.gamma = gamma
        self.target_update_period = target_update_period
        self.update_period = update_period

        self.build_model()
        self.cur_train_step = 0

        if debug_mode:
            self.summary = tf.summary.FileWriter(os.path.join(self.logger.output_dir, "logs"))

        self.sess = tf.Session()
        self.loss = tf.placeholder(tf.float32, shape=[])
        self.q = tf.placeholder(tf.float32, shape=[None, self.env.action_space.n])
        self.q_target = tf.placeholder(tf.float32, shape=[None, self.env.action_space.n])
        self.target_q = tf.placeholder(tf.float32, shape=[None, self.env.action_space.n])
        tf.summary.scalar("loss", self.loss)
        tf.summary.histogram("q", self.q)
        tf.summary.histogram("q_target", self.q_target)
        tf.summary.histogram("target_q", self.target_q)
        self.merge = tf.summary.merge_all()



    def build_model(self):
        self.input_shape = self.env.observation_space.shape
        self.model, self.model_target = mlp_dqn(self.env.action_space.n, self.input_shape)
        self.model_target.set_weights(self.model.get_weights())

    def choose_action(self, s, eval_mode=False):
        epsilon = self.epsilon_eval if eval_mode \
            else linearly_decaying_epsilon(self.epsilon_decay_period, self.cur_train_step, self.warmup_steps, self.epsilon_train)
        # print("epsilon:", epsilon)
        if random.random() <= 1-epsilon:
            q = self.model.predict(s[np.newaxis, :])
            a = np.argmax(q, axis=1)[0]
            # print()
        else:
            a = self.env.action_space.sample()

        return a

    def run_one_phrase(self, min_step, eval_mode=False):
        step = 0
        episode = 0
        reward = 0.

        while step < min_step:
            reward_episode = 0.
            step_episode = 0
            done = False
            obs = self.env.reset()
            # o = np.array(obs)

            while not done:
                a = self.choose_action(np.array(obs), eval_mode)
                obs_, r, done, _ = self.env.step(a)

                step += 1
                step_episode += 1
                reward += r
                reward_episode += r

                if not eval_mode:
                    self.cur_train_step += 1
                    self.replay_buffer.add(np.array(obs), a, np.array(obs_), r, done)

                    if self.cur_train_step > 100:
                        if self.cur_train_step % self.update_period == 0:
                            # data = self.replay_buffer.sample()
                            (s, a, s_, r, d) = self.replay_buffer.sample()
                            target_q = self.model_target.predict(s_)
                            q_ = np.max(target_q, axis=1)

                            q_target = r + (1-d) *self.gamma * q_

                            q = self.model.predict(s)
                            ori_q = np.copy(q)
                            batch_index = np.arange(self.batch_size)
                            q[batch_index, a] = q_target

                            result = self.model.train_on_batch(np.array(s), q)

                            if debug_mode:
                                merge = self.sess.run(self.merge,
                                                      feed_dict={self.loss: result[0], self.q: ori_q, self.q_target: q,
                                                                 self.target_q: target_q})
                                self.summary.add_summary(merge, (self.cur_train_step-100)/self.update_period)
                            # print("result:", result)

                        if self.cur_train_step % self.target_update_period == 0:
                            self.model_target.set_weights(self.model.get_weights())

                if step_episode >= self.max_ep_len:
                    break
                obs = obs_

            episode += 1

            # print("ep:", episode, "step:", step, "r:", reward)
            if not eval_mode:
                self.logger.store(step=step_episode, reward=reward_episode)

        return reward, episode

    def train_test(self):
        for i in range(self.iteration):
            print("iter:", i+1)
            self.logger.store(iter=i+1)
            reward, episode = self.run_one_phrase(self.train_step)
            print("reward:", reward/episode, "episode:", episode)

            self.logger.log_tabular("iter", i+1)
            self.logger.log_tabular("reward", with_min_and_max=True)
            self.logger.log_tabular("step", with_min_and_max=True)
            self.logger.dump_tabular()

            reward, episode = self.run_one_phrase(self.evaluation_step, True)
            print("reward:", reward / episode, "episode:", episode)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--env', type=str, default='CartPole-v1')
    parser.add_argument('--seed', '-s', type=int, default=0)
    parser.add_argument('--exp_name', type=str, default='dqn_cartpole')
    args = parser.parse_args()

    from utils.run_utils import setup_logger_kwargs
    logger_kwargs = setup_logger_kwargs(args.exp_name, args.seed)

    dqn = Dqn(args.env, logger_kwargs=logger_kwargs)
    dqn.train_test()


