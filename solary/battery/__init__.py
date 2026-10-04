"""Home battery controller: when to charge, when to discharge and when to sell, every 15 minutes.

The house has solar panels (the roof found by `solary.analyze`), a battery, a grid connection
with 15-minute market prices (RCE, published by PSE the day before) and possibly a weak local
grid that trips the inverter when too much is exported at a sunny midday. The physics is not
linear (inverter losses depend on power, wear on how the battery is used, trips are all or
nothing), and forecasts are uncertain. An agent trained with reinforcement learning (imitation of
a planner, then PPO) picks one of 11 options from the battery's charge, the next 36 hours of
prices, the solar forecast and its uncertainty, and the expected consumption.

    data.py        prices (PSE RCE), weather (NASA POWER history, Open-Meteo forecast), PV, load
    model.py       nonlinear battery + inverter + grid physics, tariffs, forecasts, observation
    strategies.py  no battery, the usual inverter rule, linear MPC, nonlinear MPC (dynamic
                   programming on forecasts), the exact optimum (dynamic programming, future known)
    env.py         Gymnasium environment for training (needs the "rl" dependency group)
    train.py       imitation + PPO with randomised houses; exports the policy to numpy (policy.npz)
    policy.py      the trained policy in plain numpy: no torch needed to run it
    evaluate.py    all strategies on a test year the agent has never seen
    plan.py        the plan for today and tomorrow from live prices and the weather forecast

See README.md, section "Magazyn energii".
"""
