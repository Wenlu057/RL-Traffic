# Import necessary libraries
import os
import sys
import numpy as np
import matplotlib

matplotlib.use('Agg')

import matplotlib.pyplot as plt
import gymnasium as gym
from gymnasium import spaces
import traci
from stable_baselines3 import DQN
# from stable_baselines3.common.callbacks import BaseCallback
from stable_baselines3.common.callbacks import BaseCallback, EvalCallback

# Step 2: Establish path to SUMO (SUMO_HOME)
if 'SUMO_HOME' in os.environ:
    tools = os.path.join(os.environ['SUMO_HOME'], 'tools')
    sys.path.append(tools)
else:
    sys.exit("Please declare environment variable 'SUMO_HOME'")

# Step 3: Define SUMO configuration
Sumo_config = [
    'sumo',  # Use 'sumo' for non-GUI mode
    '-c', 'config/light.sumocfg',  # goes one directory up
    # '--step-length', '0.1',
    '--delay', '1000',
    '--lateral-resolution', '0'
]

# Step 4: Define Custom SUMO Environment
class SumoEnv(gym.Env):
    def __init__(self, config):
        super(SumoEnv, self).__init__()
        self.config = config
        self.action_space = spaces.Discrete(2)  # 0 = keep phase, 1 = switch phase
        self.observation_space = spaces.Box(low=0, high=np.inf, shape=(7,), dtype=np.float32)  # (q_EB_0, q_EB_1, q_EB_2, q_SB_0, q_SB_1, q_SB_2, current_phase)
        self.min_green_steps = 5  # 10s for primary green phases (0 and 3)
        self.min_yellow_steps = 3  # 3s for yellow phases (2 and 5)
        self.min_pedestrian_steps = 5  # 5s for pedestrian/transition green phases (1 and 4)
        self.step_count = 0
        self.max_steps = 1000  # Steps per episode
        self.cumulative_reward = 0.0
        self.total_queue = 0.0
        self.last_switch_step = -self.min_green_steps
        self.current_simulation_step = 0
        self.episode_count = 0
        # Lists to record data for plotting
        self.episode_history = []
        self.reward_history = []
        self.queue_history = []
        self.braking_history = []

    # Each time an episode is done, we reset the environment
    def reset(self, seed=None, **kwargs):
        # Close any existing SUMO connection
        if traci.isLoaded():
            traci.close()
        # Start new SUMO simulation
        traci.start(self.config)
        self.step_count = 0
        self.cumulative_reward = 0.0
        self.total_queue = 0.0
        self.last_switch_step = -self.min_green_steps
        self.current_simulation_step = 0
        self.episode_count += 1
        state = self._get_state()
        info = {"episode": self.episode_count}
        return state, info

    # Functions where a simualtion step is taken, action prompted and reward calculated
    # Computes cumulative reward and avg. queue length
    def step(self, action):
        self.current_simulation_step = self.step_count
        self._apply_action(action)
        traci.simulationStep()
        new_state = self._get_state()
        reward = self._get_reward(new_state)
        self.cumulative_reward += reward
        self.total_queue += sum(new_state[:-1])
        self.step_count += 1

        terminated = False
        truncated = self.step_count >= self.max_steps
        done = terminated or truncated

        info = {}
        if done:
            avg_queue = self.total_queue / self.step_count if self.step_count > 0 else 0
            self.episode_history.append(self.episode_count - 1)
            self.reward_history.append(self.cumulative_reward)
            self.queue_history.append(avg_queue)
            info = {
                "episode": self.episode_count,
                "cumulative_reward": self.cumulative_reward,
                "avg_queue_length": avg_queue
            }
            print(f"Episode {info['episode']} Summary: Cumulative Reward: {info['cumulative_reward']:.2f}, Avg Queue Length: {info['avg_queue_length']:.2f}")

        return new_state, reward, terminated, truncated, info

    # Get state values from detectors
    def _get_state(self):
        detector_EB_0 = "e2_2"
        detector_SB_0 = "e2_3"
        detector_SB_1 = "e2_4"
        detector_WB_0 = "e2_6"
        detector_NB_0 = "e2_11"
        detector_NB_1 = "e2_9"
        traffic_light_id = "41896158"

        q_EB_0 = self._get_queue_length(detector_EB_0)
        q_SB_0 = self._get_queue_length(detector_SB_0)
        q_SB_1 = self._get_queue_length(detector_SB_1)
        q_WB_0 = self._get_queue_length(detector_WB_0)
        q_NB_0 = self._get_queue_length(detector_NB_0)
        q_NB_1 = self._get_queue_length(detector_NB_1)

        current_phase = self._get_current_phase(traffic_light_id)

        return np.array([q_EB_0, q_SB_0, q_SB_1, q_WB_0, q_NB_0, q_NB_1, current_phase], dtype=np.float32)

    # def _apply_action(self, action, tls_id="41896158"):
    #     if action == 0:
    #         return
    #     elif action == 1:
    #         if self.current_simulation_step - self.last_switch_step >= self.min_green_steps:
    #             current_phase = self._get_current_phase(tls_id)
    #             # Switch between phase 0 and phase 2 only
    #             next_phase = 2 if current_phase == 0 else 0
    #             traci.trafficlight.setPhase(tls_id, next_phase)
    #             self.last_switch_step = self.current_simulation_step

    # Apply the next action when prompted. Switching between one phase to another requires a buffer time.
    # **NEED TO FIX**
    def _apply_action(self, action, tls_id="41896158"):
        if action == 0:
            return
        elif action == 1:
            if self.current_simulation_step - self.last_switch_step >= self.min_green_steps:
                current_phase = self._get_current_phase(tls_id)
                try:
                    program = traci.trafficlight.getAllProgramLogics(tls_id)[0]
                    num_phases = len(program.phases)
                    if num_phases == 0:
                        return
                    # Increment phase by 1 modulo total phases
                    next_phase = (current_phase + 1) % num_phases
                    traci.trafficlight.setPhase(tls_id, next_phase)
                    self.last_switch_step = self.current_simulation_step
                except traci.exceptions.TraCIException:
                    # Handle possible SUMO connection issues gracefully
                    pass
    
    # def _apply_action(self, action, tls_id="41896158"):
    #     if action == 0:
    #         return  # Keep current phase
    #     elif action == 1:
    #         current_phase = self._get_current_phase(tls_id)
    #         # Enforce minimum durations based on phase type
    #         if current_phase in [0, 3]:  # Primary green phases
    #             if self.current_simulation_step - self.last_switch_step < self.min_green_steps:
    #                 # print(f"Cannot switch: Phase {current_phase} (green) has not reached min_green_steps ({self.min_green_steps})")
    #                 return
    #         elif current_phase in [2, 5]:  # Yellow phases
    #             if self.current_simulation_step - self.last_switch_step < self.min_yellow_steps:
    #                 # print(f"Cannot switch: Phase {current_phase} (yellow) has not reached min_yellow_steps ({self.min_yellow_steps})")
    #                 return
    #         elif current_phase in [1, 4]:  # Pedestrian/transition green phases
    #             if self.current_simulation_step - self.last_switch_step < self.min_pedestrian_steps:
    #                 # print(f"Cannot switch: Phase {current_phase} (pedestrian) has not reached min_pedestrian_steps ({self.min_pedestrian_steps})")
    #                 return
    #         # # Check if vehicles are approaching to avoid abrupt red light
    #         # if self._vehicles_approaching(tls_id):
    #         #     print(f"Cannot switch: Vehicles approaching in phase {current_phase}")
    #         #     return
    #         try:
    #             program = traci.trafficlight.getAllProgramLogics(tls_id)[0]
    #             num_phases = len(program.phases)
    #             if num_phases == 0:
    #                 return
    #             # Increment phase by 1 modulo total phases
    #             next_phase = (current_phase + 1) % num_phases
    #             # print(f"Switching from phase {current_phase} to phase {next_phase} at step {self.current_simulation_step}")
    #             traci.trafficlight.setPhase(tls_id, next_phase)
    #             self.last_switch_step = self.current_simulation_step
    #         except traci.exceptions.TraCIException:
    #             print("TraCIException occurred during phase switch")

    
    # Reward function computed using the queue length
    def _get_reward(self, state):
        total_queue = sum(state[:-1])  # Exclude current_phase
        reward = -float(total_queue)
        return reward

    # TraCI call for queue data
    def _get_queue_length(self, detector_id):
        return traci.lanearea.getLastStepVehicleNumber(detector_id)

    # TraCI call for phase data
    def _get_current_phase(self, tls_id):
        return traci.trafficlight.getPhase(tls_id)

    # TraCI call for closing
    def close(self):
        if traci.isLoaded():
            traci.close()

    # For non-GUI mode
    def render(self, mode="human"):
        pass  # No rendering for non-GUI SUMO

# Step 5: Custom Callback for Episode Control
class EpisodeCallback(BaseCallback):
    def __init__(self, env, total_episodes=30, verbose=0):
        super(EpisodeCallback, self).__init__(verbose)
        self.env = env
        self.total_episodes = total_episodes
        self.current_episode = 0

    def _on_step(self):
        if self.env.step_count >= self.env.max_steps:
            self.current_episode += 1
            if self.current_episode >= self.total_episodes:
                return False  # Stop training
        return True


# Step 6: Episode-based Training Loop with Stable Baselines3
print("\n=== Starting Episode-based Reinforcement Learning (DQN with Stable Baselines3) ===")

# Initialize environment
env = SumoEnv(Sumo_config)

# Check environment compatibility
# from stable_baselines3.common.env_checker import check_env
# check_env(env)

# Initialize DQN model
model = DQN(
    policy="MlpPolicy",
    env=env,
    learning_rate=0.0001,  # Fixed: 10e-5 was too low, use 0.0001
    gamma=0.99,            # Increased from 0.9 to 0.99 for better long-term planning
    exploration_initial_eps=1,  # Start with 100% exploration
    exploration_final_eps=0.01,   # End with 1% exploration
    exploration_fraction=0.3,     # Decay over 30% of training
    verbose=1,
    learning_starts=5000,         # Start learning after 1000 steps
    train_freq=2,                 # Update every 8 steps (balanced)
    batch_size=128,                # Increased batch size
    target_update_interval=1000,   # Update target network more frequently
    buffer_size=100000,           # Larger replay buffer
    tau=0.01,                      # Hard target update
    gradient_steps=2              # Gradient steps per update
)

# Train for exactly 100 episodes
TOTAL_EPISODES = 100
episode_callback = EpisodeCallback(env, total_episodes=TOTAL_EPISODES, verbose=1)

eval_env = SumoEnv(Sumo_config)
eval_callback = EvalCallback(
    eval_env,
    best_model_save_path='./logs/best_model',
    log_path='./logs/results',
    eval_freq=env.max_steps,
    n_eval_episodes=1,
    deterministic=True,
    render=False
)
callbacks = [episode_callback]
model.learn(total_timesteps=TOTAL_EPISODES * env.max_steps, callback=callbacks, progress_bar=True)

# Save the model
model.save("dqn_sumo")

# For DQN (DQN_working.py, after updating)
np.save("dqn_episode_history.npy", env.episode_history)
np.save("dqn_reward_history.npy", env.reward_history)
np.save("dqn_queue_history.npy", env.queue_history)


# Close the environment
env.close()

# Step 7: Visualization of Results
plt.figure(figsize=(10, 6))
plt.plot(env.episode_history, env.reward_history, marker='o', linestyle='-', label="Cumulative Reward")
plt.xlabel("Episode")
plt.ylabel("Cumulative Reward")
plt.title("RL Training (DQN): Cumulative Reward over Episodes")
plt.legend()
plt.grid(True)
plt.savefig("cumulative_reward_DQN.png")

plt.figure(figsize=(10, 6))
plt.plot(env.episode_history, env.queue_history, marker='o', linestyle='-', label="Average Queue Length")
plt.xlabel("Episode")
plt.ylabel("Average Queue Length")
plt.title("RL Training (DQN): Average Queue Length over Episodes")
plt.legend()
plt.grid(True)
plt.savefig("queue_length_DQN.png")
