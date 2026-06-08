import matplotlib.pyplot as plt

def plot_winner_probabilities(winner_probs, save_path=None, top_n=15):
    top_winners = winner_probs.head(top_n).copy()
    
    plt.figure(figsize=(10, 6))
    plt.barh(top_winners["country"], top_winners["win_probability"])
    plt.gca().invert_yaxis()
    plt.title("2026 World Cup Winner Probability - Real Bracket Simulation")
    plt.xlabel("Win Probability")
    plt.ylabel("Country")
    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=200)
    plt.show()
