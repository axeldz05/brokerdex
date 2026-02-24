# Brokerdex: Investment Platform with Pokemon!

**Brokerdex** is an investment plaform where it works with pokemon/creatures instead of traditional financial assets. The goal is to adapt the known financial concepts into a different environment.  


## Key Features
* **Handling multiple battles:** Using Celery to schedule battles between the creatures and updating their value in price. 
* **Hatch, train, and evolution:** Possibility to hatch, train and evolutionize a creature in the dashboard.

## To-Do

### Core
- [x] Authentication, User dashboard, and a mocked withdraw/deposit.
- [x] Creature model, listing, admin creation of creature, posession of Creature by User.
- [ ] Dynamic Pricing Engine: Implement a "Stock Price" logic for creatures where their value fluctuates based on battle performance, rarity, and market demand.
- [ ] Order Book: Simple Buy/Sell system to trade creature "shares" or ownership between users.

### Simulation and Creature Tasks
- [ ] Async Battle Engine: A Celery-based worker that simulates battles in the background. Results (Win/Loss/Draw) should trigger a signal to update the creature's `current_price`.
- [ ] Incubation System: A timed task (Celery) where an egg "hatches" after a certain period, minting a new creature with randomized base stats (IVs).
- [ ] Training Mechanism: Spend "In-game currency" to trigger a training task that improves base stats, acting like a "Capital Improvement" on the asset.

### Statistics and Further Analysis Tools
- [ ] Price History Charts: Integration with a charting library (like Chart.js or D3.js) to show the price evolution of a creature over time.
- [ ] Market Indices: Create Indices like a "Type-based Index"
- [ ] Volatility Alerts: A notification system to alert users when a creature they own drops or rises in value by more than 10% due to battle streaks.
- [ ] Portfolio Analytics: A dashboard section showing Profit/Loss (P&L), ROI, and asset distribution.

---
