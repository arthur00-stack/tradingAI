import torch
from torch.distributions import Categorical
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim

from fetch_data import load_data
from trading_env import StockTradingEnv


class LSTMTradingActorCritic(nn.Module):
    """Réseau de neurones récurrent LSTM (Actor-Critic)."""

    def __init__(self, input_dim=5, hidden_dim=64, num_layers=2, action_dim=3):
        super(LSTMTradingActorCritic, self).__init__()
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers

        self.lstm = nn.LSTM(
            input_size=input_dim,
            hidden_size=hidden_dim,
            num_layers=num_layers,
            batch_first=True,
        )

        self.fc_shared = nn.Linear(hidden_dim, 32)
        self.actor_head = nn.Linear(32, action_dim)
        self.critic_head = nn.Linear(32, 1)

    def forward(self, x, hidden_state=None):
        lstm_out, hidden_state = self.lstm(x, hidden_state)
        last_step = lstm_out[:, -1, :]
        shared = F.relu(self.fc_shared(last_step))

        action_logits = self.actor_head(shared)
        state_value = self.critic_head(shared)

        return action_logits, state_value, hidden_state

    def init_hidden(self, batch_size=1, device="cpu"):
        h0 = torch.zeros(self.num_layers, batch_size, self.hidden_dim).to(
            device
        )
        c0 = torch.zeros(self.num_layers, batch_size, self.hidden_dim).to(
            device
        )
        return (h0, c0)


def train():
    df = load_data("AAPL", start="2020-01-01", end="2023-01-01")
    env = StockTradingEnv(df)

    model = LSTMTradingActorCritic(
        input_dim=5, hidden_dim=64, num_layers=2, action_dim=3
    )
    optimizer = optim.Adam(model.parameters(), lr=0.001)

    epochs = 30
    gamma = 0.99

    print("\n--- Début de l'entraînement de l'IA Trading (avec RSI) ---")

    for epoch in range(epochs):
        obs, _ = env.reset()
        hidden = model.init_hidden(batch_size=1)

        log_probs, values, rewards, entropies = [], [], [], []
        done = False

        while not done:
            state_tensor = torch.tensor(obs, dtype=torch.float32).unsqueeze(0)
            logits, value, hidden = model(state_tensor, hidden)
            hidden = (hidden[0].detach(), hidden[1].detach())

            dist = Categorical(logits=logits)
            action = dist.sample()

            next_obs, reward, done, _, info = env.step(action.item())

            log_probs.append(dist.log_prob(action))
            values.append(value.squeeze(0))
            rewards.append(reward)
            entropies.append(dist.entropy())

            obs = next_obs

        R = 0
        returns = []
        for r in rewards[::-1]:
            R = r + gamma * R
            returns.insert(0, R)

        returns = torch.tensor(returns, dtype=torch.float32)
        values = torch.cat(values).squeeze(-1)
        log_probs = torch.cat(log_probs)

        advantage = returns - values.detach()
        actor_loss = -(log_probs * advantage).mean()
        critic_loss = F.mse_loss(values, returns)
        entropy_loss = -0.01 * torch.cat(entropies).mean()

        total_loss = actor_loss + critic_loss + entropy_loss

        optimizer.zero_grad()
        total_loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), 0.5)
        optimizer.step()

        if (epoch + 1) % 5 == 0 or epoch == 0:
            print(
                f"Époque {epoch+1:02d}/{epochs} | Portefeuille : {info['net_worth']:.2f}$ | Loss : {total_loss.item():.4f}"
            )

    torch.save(model.state_dict(), "lstm_trading_model.pth")
    print(
        "\n✅ Entraînement terminé ! Modèle sauvegardé sous 'lstm_trading_model.pth'."
    )


if __name__ == "__main__":
    train()