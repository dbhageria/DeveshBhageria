//+------------------------------------------------------------------+
//| HeikenAshiConvictionEA.mq5                                       |
//| Gold Heikin Ashi multi-timeframe conviction EA.                  |
//|                                                                  |
//| Eight frames vote with their last CLOSED Heikin Ashi candle:     |
//| M1, M5, M15, M30, M45 (built from M1, anchored to server         |
//| midnight), H1, H4, D1. Entry needs a fresh M1 no-wick signal     |
//| and at least InpMinVotes agreeing frames. Risk tiers by votes:   |
//| 5-6 = 0.5%, 7 = 1%, 8 = 2% of equity. The basket is split into   |
//| InpLegs equal positions with one broker-side stop beyond the     |
//| last InpStopM5Bars completed M5 candles. Exit: trailing stop     |
//| (activate / distance in R) or a fixed TP in R.                   |
//|                                                                  |
//| DEMO ACCOUNTS AND STRATEGY TESTER ONLY. Hedging accounts only.   |
//|                                                                  |
//| Defaults are the setting that held up best in the 2011-2026      |
//| research (ea-research/HA_RESEARCH.md): 8/8 votes, stop beyond 24 |
//| M5 candles, fixed 1.5R target, London-NY hours. The original     |
//| specification (5 votes, 5 M5 candles, trailing 1R/1R, all hours) |
//| lost money in every year tested; it is reachable via the inputs. |
//+------------------------------------------------------------------+
#property copyright "EA research - Heiken Ashi conviction"
#property version   "1.00"

#include <Trade/Trade.mqh>

enum EVoteMode
{
   VOTE_NO_WICK = 0, // No-wick candle in the signal direction (specification)
   VOTE_COLOUR  = 1  // Any HA candle of the signal colour
};

input group "Signals"
input int       InpHAHistoryBars   = 200;          // HA history per frame (candles)
input EVoteMode InpVoteMode        = VOTE_NO_WICK; // What counts as a vote
input int       InpMinVotes        = 8;            // Minimum agreeing frames of 8 (M1 included; spec 5)
input int       InpWickTolTicks    = 1;            // No-wick tolerance (ticks)
input int       InpMinBodyTicks    = 2;            // Minimum HA body (ticks)
input bool      InpAllowBuys       = true;         // Allow buy baskets
input bool      InpAllowSells      = true;         // Allow sell baskets

input group "Risk"
input double InpRisk5to6           = 0.5;    // Risk % of equity at 5-6 votes
input double InpRisk7              = 1.0;    // Risk % of equity at 7 votes
input double InpRisk8              = 2.0;    // Risk % of equity at 8 votes
input double InpRiskHardCap        = 2.0;    // Hard cap on basket risk % (max 2)
input int    InpLegs               = 5;      // Requested positions per basket
input double InpCostPerLot         = 10.0;   // Cost reserve per lot, account currency (round trip)
input int    InpStopSlipPoints     = 20;     // Adverse stop slippage reserve (points)
input double InpMaxMarginPct       = 25.0;   // Max margin use, % of equity
input bool   InpRequireFlatAccount = true;   // Block new basket while ANY position/order exists

input group "Stop / exit"
input int    InpStopM5Bars         = 24;     // Completed M5 candles for the initial stop (spec 5)
input double InpStopBuffer         = 0.10;   // Stop buffer (price units)
input double InpMaxSpread          = 1.00;   // Max spread at entry (price units)
input bool   InpUseTrailing        = false;  // true = trailing stop (spec), false = fixed TP
input double InpTrailActivateR     = 1.0;    // Trailing starts after this many R in profit
input double InpTrailDistanceR     = 1.0;    // Trailing distance (R)
input int    InpTrailMinStepTicks  = 5;      // Minimum stop move (ticks)
input double InpFixedTP_R          = 1.5;    // Fixed take-profit (R), when trailing is off

input group "Session (server time). 10-23 on a GMT+2/+3 broker = 07-20 UTC in summer"
input bool   InpUseSession         = true;   // Only open baskets inside the session (spec: off)
input int    InpSessionStartHour   = 10;     // Session start hour (server)
input int    InpSessionEndHour     = 23;     // Session end hour (server, exclusive)

input group "Execution / panel"
input bool   InpEnableAutoEntries  = false;  // Auto entries on at start
input ulong  InpMagic              = 26110511; // Magic number
input int    InpDeviationPoints    = 30;     // Max deviation for market orders (points)
input bool   InpShowPanel          = true;   // Show status panel and buttons

CTrade   trade;
bool     g_auto = false;
datetime g_lastBar = 0;
string   g_status = "starting";
int      g_states[8];
bool     g_avail[8];
string   g_names[8] = {"M1", "M5", "M15", "M30", "M45", "H1", "H4", "D1"};
ENUM_TIMEFRAMES g_tfs[8] = {PERIOD_M1, PERIOD_M5, PERIOD_M15, PERIOD_M30, PERIOD_CURRENT,
                            PERIOD_H1, PERIOD_H4, PERIOD_D1}; // index 4 = synthetic M45
string   g_gvPrefix;
const string BTN_AUTO  = "HACE_btn_auto";
const string BTN_CLOSE = "HACE_btn_close";

//+------------------------------------------------------------------+
int OnInit()
{
   bool tester = (bool)MQLInfoInteger(MQL_TESTER);
   if(!tester && AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_REAL)
   {
      Alert("HeikenAshiConvictionEA runs on demo accounts and in the Strategy Tester only.");
      return INIT_FAILED;
   }
   if(AccountInfoInteger(ACCOUNT_MARGIN_MODE) != ACCOUNT_MARGIN_MODE_RETAIL_HEDGING)
   {
      Alert("HeikenAshiConvictionEA needs a hedging account.");
      return INIT_FAILED;
   }
   if(InpMinVotes < 5 || InpMinVotes > 8 || InpLegs < 1 || InpStopM5Bars < 1 ||
      InpRiskHardCap <= 0 || InpRiskHardCap > 2.0 || InpHAHistoryBars < 20)
   {
      Print("Invalid inputs: MinVotes 5-8, Legs >= 1, StopM5Bars >= 1, hard cap 0-2%, history >= 20");
      return INIT_PARAMETERS_INCORRECT;
   }
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpDeviationPoints);
   g_auto = InpEnableAutoEntries;
   g_gvPrefix = StringFormat("HACE_%I64d_%s_%s_%I64u_", AccountInfoInteger(ACCOUNT_LOGIN),
                             AccountInfoString(ACCOUNT_SERVER), _Symbol, InpMagic);
   if(InpShowPanel && !tester)
      CreateButtons();
   EventSetTimer(1);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   ObjectDelete(0, BTN_AUTO);
   ObjectDelete(0, BTN_CLOSE);
   Comment("");
}

//+------------------------------------------------------------------+
//| Heikin Ashi                                                      |
//+------------------------------------------------------------------+
// rates oldest first; returns state of the LAST candle (+1 buy, -1 sell, 0 none) and, optionally,
// of the one before it.
int HAState(const double &o[], const double &h[], const double &l[], const double &c[], int n,
            int &prevState)
{
   double tick = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tol = InpWickTolTicks * tick, minBody = InpMinBodyTicks * tick;
   double haO = (o[0] + c[0]) / 2.0, haC = (o[0] + h[0] + l[0] + c[0]) / 4.0;
   int state = 0;
   prevState = 0;
   for(int i = 0; i < n; i++)
   {
      if(i > 0)
      {
         haO = (haO + haC) / 2.0;
         haC = (o[i] + h[i] + l[i] + c[i]) / 4.0;
      }
      double haH = MathMax(h[i], MathMax(haO, haC));
      double haL = MathMin(l[i], MathMin(haO, haC));
      double body = haC - haO;
      int s = 0;
      if(body >= minBody)
         s = (InpVoteMode == VOTE_COLOUR || MathMin(haO, haC) - haL <= tol) ? 1 : 0;
      else if(-body >= minBody)
         s = (InpVoteMode == VOTE_COLOUR || haH - MathMax(haO, haC) <= tol) ? -1 : 0;
      prevState = state;
      state = s;
   }
   return state;
}

// M1 signal always uses the strict no-wick rule (the specification's entry trigger)
int HAStrict(const double &o[], const double &h[], const double &l[], const double &c[], int n,
             int &prevState)
{
   double tick = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tol = InpWickTolTicks * tick, minBody = InpMinBodyTicks * tick;
   double haO = (o[0] + c[0]) / 2.0, haC = (o[0] + h[0] + l[0] + c[0]) / 4.0;
   int state = 0;
   prevState = 0;
   for(int i = 0; i < n; i++)
   {
      if(i > 0)
      {
         haO = (haO + haC) / 2.0;
         haC = (o[i] + h[i] + l[i] + c[i]) / 4.0;
      }
      double haH = MathMax(h[i], MathMax(haO, haC));
      double haL = MathMin(l[i], MathMin(haO, haC));
      double body = haC - haO;
      int s = 0;
      if(body >= minBody && MathMin(haO, haC) - haL <= tol)
         s = 1;
      else if(-body >= minBody && haH - MathMax(haO, haC) <= tol)
         s = -1;
      prevState = state;
      state = s;
   }
   return state;
}

// Native frame: last closed candle state. False if history is missing or the latest expected
// candle has not arrived (session gap or loading).
bool NativeState(ENUM_TIMEFRAMES tf, datetime decision, int &state, int &prevState, bool strict)
{
   MqlRates r[];
   ArraySetAsSeries(r, false);
   int got = CopyRates(_Symbol, tf, 1, InpHAHistoryBars, r);   // shift 1 = last closed candle
   if(got < 20)
      return false;
   int ps = PeriodSeconds(tf);
   datetime lastClose = r[got - 1].time + ps;
   if(lastClose > decision)
      return false;                                            // not closed yet at decision time
   if(ps <= 3600 && decision - lastClose >= ps)
      return false;                                            // latest expected candle missing
   double o[], h[], l[], c[];
   ArrayResize(o, got); ArrayResize(h, got); ArrayResize(l, got); ArrayResize(c, got);
   for(int i = 0; i < got; i++)
   {
      o[i] = r[i].open; h[i] = r[i].high; l[i] = r[i].low; c[i] = r[i].close;
   }
   state = strict ? HAStrict(o, h, l, c, got, prevState) : HAState(o, h, l, c, got, prevState);
   return true;
}

// Synthetic M45 from M1, buckets anchored to server midnight. Only complete 45-minute buckets are
// used, and the latest expected bucket must be complete.
bool M45State(datetime decision, int &state)
{
   int want = InpHAHistoryBars * 45 * 3 / 2 + 90;              // oversample for session gaps
   MqlRates r[];
   ArraySetAsSeries(r, false);
   int got = CopyRates(_Symbol, PERIOD_M1, 1, want, r);
   if(got < 45 * 20)
      return false;
   double o[], h[], l[], c[];
   ArrayResize(o, got / 45 + 2); ArrayResize(h, got / 45 + 2);
   ArrayResize(l, got / 45 + 2); ArrayResize(c, got / 45 + 2);
   int n = 0, count = 0;
   long curBucket = -1;
   datetime lastBucketEnd = 0;
   double bo = 0, bh = 0, bl = 0, bc = 0;
   for(int i = 0; i < got; i++)
   {
      long t = (long)r[i].time;
      long bucket = (t / 86400) * 32 + (t % 86400) / 2700;
      if(bucket != curBucket)
      {
         if(curBucket >= 0 && count == 45)
         {
            o[n] = bo; h[n] = bh; l[n] = bl; c[n] = bc; n++;
            lastBucketEnd = (datetime)((curBucket / 32) * 86400 + (curBucket % 32) * 2700 + 2700);
         }
         curBucket = bucket; count = 0;
         bo = r[i].open; bh = r[i].high; bl = r[i].low;
      }
      bh = MathMax(bh, r[i].high);
      bl = MathMin(bl, r[i].low);
      bc = r[i].close;
      count++;
   }
   long endCur = (curBucket / 32) * 86400 + (curBucket % 32) * 2700 + 2700;
   if(curBucket >= 0 && count == 45 && endCur <= (long)decision)
   {
      o[n] = bo; h[n] = bh; l[n] = bl; c[n] = bc; n++;
      lastBucketEnd = (datetime)endCur;
   }
   if(n < 20)
      return false;
   // the most recent bucket that should have closed by the decision time must be the one we have
   // (a day is exactly 32 buckets, so flooring to 2700 s keeps the server-midnight anchor)
   long expectedEnd = ((long)decision / 2700) * 2700;
   if((long)lastBucketEnd != expectedEnd)
      return false;
   int prev;
   state = HAState(o, h, l, c, n, prev);
   return true;
}

//+------------------------------------------------------------------+
//| Positions                                                        |
//+------------------------------------------------------------------+
int OwnPositions(double &avgPrice, double &volume, int &dir, double &sl)
{
   int n = 0;
   double pv = 0;
   volume = 0; dir = 0; sl = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol || PositionGetInteger(POSITION_MAGIC) != (long)InpMagic)
         continue;
      double v = PositionGetDouble(POSITION_VOLUME);
      pv += v * PositionGetDouble(POSITION_PRICE_OPEN);
      volume += v;
      dir = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? 1 : -1;
      sl = PositionGetDouble(POSITION_SL);
      n++;
   }
   avgPrice = volume > 0 ? pv / volume : 0;
   return n;
}

void CloseOwn(string why)
{
   Print("Closing HA basket: ", why);
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == (long)InpMagic)
         if(!trade.PositionClose(ticket, InpDeviationPoints))
            Print("Close failed for ", ticket, ": ", trade.ResultRetcodeDescription(), " (will retry)");
   }
}

bool AccountFlat()
{
   return PositionsTotal() == 0 && OrdersTotal() == 0;
}

void SaveBasket(double avg, double initialStop, int dir)
{
   GlobalVariableSet(g_gvPrefix + "avg", avg);
   GlobalVariableSet(g_gvPrefix + "stop0", initialStop);
   GlobalVariableSet(g_gvPrefix + "dir", dir);
   GlobalVariableSet(g_gvPrefix + "best", avg);
}

void ClearBasket()
{
   GlobalVariablesDeleteAll(g_gvPrefix);
}

//+------------------------------------------------------------------+
//| Entry                                                            |
//+------------------------------------------------------------------+
double NormalizeVolumeDown(double v)
{
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   return MathFloor(v / step + 1e-9) * step;
}

void TryEntry(int dir, int votes)
{
   if(!g_auto) { g_status = "signal ignored: Auto OFF"; return; }
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !MQLInfoInteger(MQL_TRADE_ALLOWED))
   { g_status = "signal ignored: Algo Trading disabled"; return; }
   if((dir > 0 && !InpAllowBuys) || (dir < 0 && !InpAllowSells))
   { g_status = "signal ignored: direction disabled"; return; }
   if(InpRequireFlatAccount && !AccountFlat())
   { g_status = "signal ignored: account not flat"; return; }
   if(InpUseSession)
   {
      MqlDateTime dt; TimeToStruct(TimeCurrent(), dt);
      if(dt.hour < InpSessionStartHour || dt.hour >= InpSessionEndHour)
      { g_status = "signal ignored: outside session"; return; }
   }
   MqlTick tk;
   if(!SymbolInfoTick(_Symbol, tk)) return;
   if(tk.ask - tk.bid > InpMaxSpread) { g_status = "signal ignored: spread too wide"; return; }

   // initial stop from the last N completed M5 candles
   MqlRates m5[];
   if(CopyRates(_Symbol, PERIOD_M5, 1, InpStopM5Bars, m5) < InpStopM5Bars)
   { g_status = "signal ignored: M5 history"; return; }
   double ext = dir > 0 ? m5[0].low : m5[0].high;
   for(int i = 1; i < InpStopM5Bars; i++)
      ext = dir > 0 ? MathMin(ext, m5[i].low) : MathMax(ext, m5[i].high);
   double entry = dir > 0 ? tk.ask : tk.bid;
   double stop = dir > 0 ? ext - InpStopBuffer : ext + InpStopBuffer;
   double minDist = (SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) + 1) * _Point;
   if(dir > 0 && entry - stop < minDist) stop = entry - minDist;
   if(dir < 0 && stop - entry < minDist) stop = entry + minDist;
   stop = NormalizeDouble(stop, _Digits);
   double risk = (entry - stop) * dir;
   if(risk <= 0) { g_status = "signal ignored: stop on wrong side"; return; }

   // risk budget
   double pct = votes >= 8 ? InpRisk8 : (votes == 7 ? InpRisk7 : InpRisk5to6);
   pct = MathMin(pct, InpRiskHardCap);
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double budget = equity * pct / 100.0;
   double slip = InpStopSlipPoints * _Point;
   double worst = dir > 0 ? stop - slip : stop + slip;
   double pnl = 0;
   if(!OrderCalcProfit(dir > 0 ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, _Symbol, 1.0, entry, worst, pnl) || pnl >= 0)
   { g_status = "signal ignored: profit calc failed"; return; }
   double lossPerLot = -pnl + InpCostPerLot;
   double totalLots = budget / lossPerLot;
   double margin1 = 0;
   if(OrderCalcMargin(dir > 0 ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, _Symbol, 1.0, entry, margin1) && margin1 > 0)
      totalLots = MathMin(totalLots, equity * InpMaxMarginPct / 100.0 / margin1);
   double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   int legs = InpLegs;
   double leg = NormalizeVolumeDown(totalLots / legs);
   while(legs > 1 && leg < vmin)
   {
      legs--;
      leg = NormalizeVolumeDown(totalLots / legs);
   }
   if(leg < vmin) { g_status = StringFormat("signal ignored: budget %.2f too small for min lot", budget); return; }
   leg = MathMin(leg, vmax);

   double tp = 0;
   if(!InpUseTrailing)
      tp = NormalizeDouble(entry + dir * InpFixedTP_R * risk, _Digits);
   int filled = 0;
   for(int k = 0; k < legs; k++)
   {
      bool ok = dir > 0 ? trade.Buy(leg, _Symbol, 0, stop, tp, StringFormat("HA %d/8", votes))
                        : trade.Sell(leg, _Symbol, 0, stop, tp, StringFormat("HA %d/8", votes));
      uint rc = trade.ResultRetcode();
      if(!ok || (rc != TRADE_RETCODE_DONE && rc != TRADE_RETCODE_PLACED))
      {
         Print("Entry leg ", k + 1, " failed: ", trade.ResultRetcodeDescription());
         g_auto = false;
         if(filled > 0)
            CloseOwn("partial basket after a failed leg");
         g_status = "entry failed: Auto paused";
         return;
      }
      filled++;
   }

   // verify fills and stops; adjust fixed TPs to each fill
   double avg, vol, sl; int d;
   int n = OwnPositions(avg, vol, d, sl);
   if(n == 0) { g_status = "no fills found after entry"; return; }
   double actualRisk = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol || PositionGetInteger(POSITION_MAGIC) != (long)InpMagic)
         continue;
      double po = PositionGetDouble(POSITION_PRICE_OPEN), psl = PositionGetDouble(POSITION_SL);
      double v = PositionGetDouble(POSITION_VOLUME), p = 0;
      if(psl == 0) { CloseOwn("position without a stop"); g_auto = false; return; }
      if(OrderCalcProfit(dir > 0 ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, _Symbol, v, po, psl, p))
         actualRisk += -p;
      if(!InpUseTrailing)
      {
         double ptp = NormalizeDouble(po + dir * InpFixedTP_R * MathAbs(po - psl), _Digits);
         trade.PositionModify(ticket, psl, ptp);
      }
   }
   if(actualRisk > budget * 1.25)
   {
      CloseOwn(StringFormat("actual risk %.2f above budget %.2f", actualRisk, budget));
      g_auto = false;
      return;
   }
   SaveBasket(avg, stop, dir);
   g_status = StringFormat("opened %s basket: %d x %.2f lots, %d/8 votes, risk %.2f%%",
                           dir > 0 ? "BUY" : "SELL", n, leg, votes, pct);
}

//+------------------------------------------------------------------+
//| Trailing                                                         |
//+------------------------------------------------------------------+
void ManageBasket()
{
   double avg, vol, sl; int dir;
   int n = OwnPositions(avg, vol, dir, sl);
   if(n == 0)
   {
      if(GlobalVariableCheck(g_gvPrefix + "dir"))
         ClearBasket();
      return;
   }
   if(!InpUseTrailing)
      return;
   if(!GlobalVariableCheck(g_gvPrefix + "stop0"))
      SaveBasket(avg, sl, dir);                                  // recover after a restart
   double stop0 = GlobalVariableGet(g_gvPrefix + "stop0");
   double R = MathAbs(avg - stop0);
   if(R <= 0) return;
   MqlTick tk;
   if(!SymbolInfoTick(_Symbol, tk)) return;
   double quote = dir > 0 ? tk.bid : tk.ask;
   double best = GlobalVariableGet(g_gvPrefix + "best");
   if((dir > 0 && quote > best) || (dir < 0 && quote < best))
   {
      best = quote;
      GlobalVariableSet(g_gvPrefix + "best", best);
   }
   if((best - avg) * dir < InpTrailActivateR * R)
      return;
   double newSL = NormalizeDouble(best - dir * InpTrailDistanceR * R, _Digits);
   double step = InpTrailMinStepTicks * SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double minDist = (MathMax(SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL),
                             SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL)) + 1) * _Point;
   if(dir > 0 && newSL > quote - minDist) newSL = NormalizeDouble(quote - minDist, _Digits);
   if(dir < 0 && newSL < quote + minDist) newSL = NormalizeDouble(quote + minDist, _Digits);
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol || PositionGetInteger(POSITION_MAGIC) != (long)InpMagic)
         continue;
      double cur = PositionGetDouble(POSITION_SL);
      bool better = dir > 0 ? newSL >= cur + step : (cur == 0 || newSL <= cur - step);
      if(better)
         trade.PositionModify(ticket, newSL, PositionGetDouble(POSITION_TP));
   }
}

//+------------------------------------------------------------------+
//| Decision on each new M1 candle                                   |
//+------------------------------------------------------------------+
void Decide()
{
   datetime decision = iTime(_Symbol, PERIOD_M1, 0);           // open of the new M1 = close of the last
   int m1prev = 0;
   bool allOk = true;
   for(int k = 0; k < 8; k++)
   {
      int s = 0, ps = 0;
      bool ok;
      if(k == 4)
         ok = M45State(decision, s);
      else
         ok = NativeState(g_tfs[k], decision, s, ps, k == 0 ? true : false);
      if(k == 0) m1prev = ps;
      g_avail[k] = ok;
      g_states[k] = ok ? s : 0;
      allOk = allOk && ok;
   }
   // M1 always uses the strict no-wick rule; its signal counts as one of the eight votes
   int signal = g_states[0];
   if(!allOk) { g_status = "frame unavailable: no entry"; return; }
   if(signal == 0 || m1prev == signal) { g_status = "waiting for a fresh M1 no-wick signal"; return; }
   if(iTime(_Symbol, PERIOD_M1, 1) - iTime(_Symbol, PERIOD_M1, 2) != 60)
   { g_status = "M1 gap: signal consumed"; return; }
   int votes = 0;
   for(int k = 0; k < 8; k++)
      if(g_states[k] == signal) votes++;
   double avg, vol, sl; int d;
   if(OwnPositions(avg, vol, d, sl) > 0) { g_status = "basket open: signal consumed"; return; }
   if(votes < InpMinVotes) { g_status = StringFormat("%s signal, %d/8 votes: no entry", signal > 0 ? "buy" : "sell", votes); return; }
   TryEntry(signal, votes);
}

void OnTick()
{
   ManageBasket();
   datetime bar = iTime(_Symbol, PERIOD_M1, 0);
   if(bar != g_lastBar)
   {
      bool first = g_lastBar == 0;
      g_lastBar = bar;
      if(!first)                                                 // never act on the attach-time signal
         Decide();
   }
   if(InpShowPanel && !MQLInfoInteger(MQL_TESTER))
      DrawPanel();
}

void OnTimer()
{
   if(InpShowPanel && !MQLInfoInteger(MQL_TESTER))
      DrawPanel();
}

//+------------------------------------------------------------------+
//| Panel                                                            |
//+------------------------------------------------------------------+
void CreateButtons()
{
   ObjectCreate(0, BTN_AUTO, OBJ_BUTTON, 0, 0, 0);
   ObjectSetInteger(0, BTN_AUTO, OBJPROP_XDISTANCE, 10);
   ObjectSetInteger(0, BTN_AUTO, OBJPROP_YDISTANCE, 230);
   ObjectSetInteger(0, BTN_AUTO, OBJPROP_XSIZE, 160);
   ObjectSetInteger(0, BTN_AUTO, OBJPROP_YSIZE, 24);
   ObjectCreate(0, BTN_CLOSE, OBJ_BUTTON, 0, 0, 0);
   ObjectSetInteger(0, BTN_CLOSE, OBJPROP_XDISTANCE, 180);
   ObjectSetInteger(0, BTN_CLOSE, OBJPROP_YDISTANCE, 230);
   ObjectSetInteger(0, BTN_CLOSE, OBJPROP_XSIZE, 160);
   ObjectSetInteger(0, BTN_CLOSE, OBJPROP_YSIZE, 24);
   ObjectSetString(0, BTN_CLOSE, OBJPROP_TEXT, "CLOSE HA BASKET");
}

void DrawPanel()
{
   ObjectSetString(0, BTN_AUTO, OBJPROP_TEXT, g_auto ? "Auto ON (click: OFF)" : "Auto OFF (click: ON)");
   string s = "Heiken Ashi Conviction EA (demo only)\n";
   int buys = 0, sells = 0;
   for(int k = 0; k < 8; k++)
   {
      string st = !g_avail[k] ? "unavailable" : (g_states[k] > 0 ? "BUY" : (g_states[k] < 0 ? "SELL" : "-"));
      s += StringFormat("  %-4s %s\n", g_names[k], st);
      if(g_avail[k] && g_states[k] > 0) buys++;
      if(g_avail[k] && g_states[k] < 0) sells++;
   }
   double avg, vol, sl; int d;
   int n = OwnPositions(avg, vol, d, sl);
   s += StringFormat("Votes: buy %d / sell %d (min %d)\nAuto: %s\nBasket: %s\nStatus: %s",
                     buys, sells, InpMinVotes, g_auto ? "ON" : "OFF",
                     n > 0 ? StringFormat("%s %d pos, %.2f lots, avg %.2f, SL %.2f", d > 0 ? "BUY" : "SELL", n, vol, avg, sl) : "none",
                     g_status);
   Comment(s);
}

void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
{
   if(id != CHARTEVENT_OBJECT_CLICK) return;
   if(sparam == BTN_AUTO)
   {
      g_auto = !g_auto;
      g_status = g_auto ? "Auto enabled: waiting for a fresh signal" : "Auto disabled";
      ObjectSetInteger(0, BTN_AUTO, OBJPROP_STATE, false);
   }
   else if(sparam == BTN_CLOSE)
   {
      g_auto = false;
      CloseOwn("panel button");
      ObjectSetInteger(0, BTN_CLOSE, OBJPROP_STATE, false);
   }
   DrawPanel();
}
//+------------------------------------------------------------------+
