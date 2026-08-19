# Factors That Cause Positive / Negative Impact on a Stock

**Date:** 2026-05-31 · For: ranking Indian stocks ("down but fundamentally good") and explaining *why* a stock moves.
Use this as the feature catalog for the scoring model. ✅ = data we can pull free (yfinance/nselib/jugaad-data); ⚠️ = needs paid API or scraping; 📰 = qualitative/news.

---

## 1. Company fundamentals (intrinsic value)

| Factor | Positive impact ↑ | Negative impact ↓ | Source |
|---|---|---|---|
| Earnings growth (EPS, PAT) | Rising YoY/QoQ earnings | Declining / missed estimates | ✅ earningsGrowth |
| Revenue growth | Top-line expansion | Stagnant/shrinking sales | ✅ revenueGrowth |
| Profit margins (gross/op/net) | Expanding margins, pricing power | Margin compression | ✅ profitMargins, operatingMargins |
| Return ratios (ROE, ROCE, ROA) | High & stable (ROE >15%) | Falling / below cost of capital | ✅ returnOnEquity, returnOnAssets |
| Debt / leverage (D/E, interest cover) | Low debt, deleveraging | High/rising debt, solvency risk | ✅ debtToEquity ⚠️ interest coverage |
| Cash flow (FCF, OCF) | Strong positive FCF | Negative/erratic cash flow | ✅ freeCashflow |
| Promoter pledge | Low/zero pledge | High/rising pledge = distress signal | ⚠️ Global Datafeeds / NSE filings |
| Dividend / buyback | Sustainable payout, buybacks | Cuts, suspension | ✅ dividendYield |
| Order book / capex (capital goods) | Strong pipeline, guided growth | Order cancellations | 📰 filings, concalls |

## 2. Valuation (is it cheap or expensive)

| Factor | Cheap / attractive | Expensive / risky | Source |
|---|---|---|---|
| P/E vs history & sector | Below own & peer median | Stretched multiples | ✅ trailingPE, forwardPE |
| P/B | Low for asset-heavy/financials | Elevated vs book | ✅ priceToBook |
| PEG | <1 (growth cheap) | >2 | ✅ pegRatio (often null) |
| EV/EBITDA | Below peers | Premium | ⚠️ compute from financials |
| Dividend yield | High & sustainable | — | ✅ dividendYield |

## 3. Ownership / "who is investing" (smart-money signal)

| Factor | Positive ↑ | Negative ↓ | Source |
|---|---|---|---|
| FII flows | Net FPI buying | Sustained FII selling | ✅ nselib NSDL FPI |
| DII / mutual fund flows | MFs accumulating | MFs trimming | ✅ nselib FII/DII; ⚠️ AMFI for stock-level |
| Promoter holding change | Promoters increasing stake | Promoters selling | ⚠️ quarterly shareholding (Screener UI / GDF) |
| Institutional holding % | Rising FII+DII % | Falling institutional interest | ⚠️ shareholding pattern |
| Bulk / block deals | Marquee investor entry | Large exits | ⚠️ NSE/BSE deals; nselib large deals |
| Superstar/known investors | Entry by respected investors | Exit | ⚠️ Trendlyne/Screener |

> **Why investor type matters:** FII conviction = global/long-term re-rating potential; DII/MF = domestic institutional validation; promoter buying = insider confidence; marquee-investor entry = qualitative endorsement. Persistent, multi-cohort accumulation while price is *down* is the strongest contrarian buy signal.

## 4. Market / macro (systematic, affects all stocks)

| Factor | Positive ↑ | Negative ↓ |
|---|---|---|
| Interest rates (RBI repo) | Cuts → growth/valuation tailwind | Hikes → de-rate, costlier debt |
| Inflation (CPI/WPI) | Cooling | High → margin & demand pressure |
| GDP / IIP growth | Accelerating | Slowdown |
| Rupee (USD/INR) | Stable/strong (importers); weak helps exporters | Sharp depreciation → FII outflow |
| Crude oil | Low (India imports) | Spikes → fiscal/margin stress |
| Global risk sentiment | Risk-on, US rate cuts | Risk-off, US yield spikes → FII selling |
| Liquidity / FII flows | Inflows | Outflows |

## 5. Sector / industry (the moat & opportunity)

| Factor | Positive ↑ | Negative ↓ |
|---|---|---|
| Sector tailwinds | Structural growth (e.g. capex, digital, premiumization) | Structural decline / disruption |
| Competitive position / moat | Market-share leader, pricing power, brand | Commoditized, eroding share |
| Regulation / policy | Favorable (PLI, tariffs, reforms) | Adverse (taxes, price caps, bans) |
| Input costs | Falling raw-material costs | Cost inflation |
| Industry cycle | Up-cycle (e.g. real estate, metals) | Down-cycle |

## 6. Company-specific events (catalysts)

| Positive catalysts ↑ | Negative catalysts ↓ |
|---|---|
| Earnings beat, raised guidance | Earnings miss, guidance cut |
| New product/capacity, large orders | Plant shutdown, demand loss |
| M&A, demerger unlocking value | Failed deal, value-destructive M&A |
| Credit-rating upgrade | Downgrade, default |
| Index inclusion (Nifty/Sensex/MSCI) | Index exclusion |
| Management quality / clean governance | Fraud, auditor resignation, related-party issues |
| Debt reduction, asset monetization | Equity dilution, QIP overhang |

## 7. Technical / sentiment (timing the "down" entry)

| Factor | Positive ↑ | Negative ↓ | Source |
|---|---|---|---|
| Price vs 52w high/low | Near low + basing | Free-falling, no support | ✅ yfinance |
| Volume | Accumulation volume | Distribution / panic | ✅ jugaad-data |
| Moving averages / RSI | Oversold + reversal | Overbought / downtrend | ✅ compute from OHLCV |
| Delivery % | Rising delivery (conviction) | Speculative churn | ⚠️ NSE bhavcopy |
| News sentiment | Improving coverage | Negative news flow | 📰 news APIs |

---

## How this maps to our thesis ("down but fundamentally good")

The ideal candidate scores **HIGH on price weakness (§7) + HIGH on fundamentals (§1) + cheap valuation (§2)**, with the kicker being **smart-money still accumulating (§3)** — i.e. price is down for *temporary/sentiment* reasons, not structural (§5/§6) deterioration.

**Red flags that mean "cheap for a reason" (avoid value traps):** rising promoter pledge, FII+promoter both exiting, structural sector decline, governance issues, persistent negative cash flow, debt spiral. These override a low valuation.

**Current scoring model** (`scripts/screen_indian_stocks.py`) covers §1, §2, §7 from yfinance.
**Next to add:** §3 ownership (nselib FII/DII + shareholding), §4 macro overlay, §6 catalysts/news.
