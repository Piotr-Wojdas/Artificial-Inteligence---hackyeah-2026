"""Home battery controller: when to charge, when to discharge and when to sell, every 15 minutes.

The house has solar panels (the roof found by `solary.analyze`), a battery and a grid connection
with hourly / 15-minute market prices (RCE, published by PSE the day before). An agent trained
with reinforcement learning (PPO) decides the battery power from the battery's state of charge,
the prices of the next 24 hours, the solar forecast and the expected consumption.

    data.py        prices (PSE RCE), weather (NASA POWER history, Open-Meteo forecast), PV, load
    model.py       house + battery physics, tariffs, forecasts the agent sees, observation
    strategies.py  no battery, the usual inverter rule, LP optimum (perfect foresight), MPC
    env.py         Gymnasium environment for training (needs the "rl" dependency group)
    train.py       PPO training with randomised houses; exports the policy to numpy (policy.npz)
    policy.py      the trained policy in plain numpy: no torch needed to run it
    evaluate.py    all strategies on a test year the agent has never seen
    plan.py        the plan for today and tomorrow from live prices and the weather forecast

See README.md, section "Magazyn energii".
"""
