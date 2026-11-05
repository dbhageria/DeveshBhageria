//+------------------------------------------------------------------+
//| LondonNYContinuation.mq5                                         |
//| XAUUSD: if London's net move exceeds a threshold, enter at the   |
//| NY open in London's direction with fixed $ SL/TP, flat by NY     |
//| close. One trade per day. Can be limited to longs or shorts.     |
//|                                                                  |
//| Defaults are the setting that was profitable in every period of  |
//| the 2011-2026 research: SHORT only, London drop > $40, SL $40,   |
//| no TP (hold to NY close), levels scaled by price ($4,450 = 1x).  |
//| Original setting: Both, 30, 40, 60, InpScaleByPrice = false.     |
//|                                                                  |
//| Session times are BROKER SERVER time. Defaults assume the usual  |
//| GMT+2 (winter) / GMT+3 (summer) gold broker, where:              |
//|   10:00 server = 08:00 London   (London open)                    |
//|   15:00 server = 08:00 New York (NY open)                        |
//|   23:55 server = ~17:00 New York (NY close)                      |
//| Check your broker's server offset and adjust if different.       |
//+------------------------------------------------------------------+
#property copyright "EA research - London/NY continuation"
#property version   "1.10"

#include <Trade/Trade.mqh>

enum ETradeSide
{
   SIDE_BOTH  = 0, // Both (buy and sell)
   SIDE_LONG  = 1, // Buy only
   SIDE_SHORT = 2  // Sell only
};

input group "Sessions (broker server time, HH:MM)"
input string InpLondonStart   = "10:00"; // London session start
input string InpNYStart       = "15:00"; // NY session start (= London measurement end, entry time)
input string InpNYEnd         = "23:55"; // NY session end (force close)
input int    InpEntryWindow   = 30;      // Minutes after NY start in which entry is allowed

input group "Signal"
input ETradeSide InpSide      = SIDE_SHORT; // Directions to trade
input double InpLondonMove    = 40.0;    // Minimum London net move ($)
input double InpMaxLondonMove = 0.0;     // Skip if London move is larger ($, 0 = off)
input bool   InpScaleByPrice  = true;    // Scale all $ levels by London open / reference price
input double InpRefPrice      = 4450.0;  // Reference gold price for scaling

input group "Trade"
input double InpStopLoss      = 40.0;    // Stop loss distance ($, 0 = none: uses fixed lots)
input double InpTakeProfit    = 0.0;     // Take profit distance ($, 0 = none: hold to NY end)
input double InpRiskPercent   = 0.5;     // Risk per trade, % of equity (0 = fixed lots)
input double InpFixedLots     = 0.01;    // Fixed lots when risk % is 0
input double InpMaxSpread     = 1.00;    // Max spread allowed at entry ($); 2026 NY-open spreads are ~$0.3-0.6
input ulong  InpMagic         = 260530;  // Magic number

CTrade trade;

//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(50);
   if(InpStopLoss < 0 || InpTakeProfit < 0 || InpLondonMove <= 0 || InpRefPrice <= 0)
   {
      Print("London move and reference price must be positive; SL and TP must be >= 0");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(At(0, InpLondonStart) >= At(0, InpNYStart) || At(0, InpNYStart) >= At(0, InpNYEnd))
   {
      Print("Sessions must satisfy LondonStart < NYStart < NYEnd within one server day");
      return INIT_PARAMETERS_INCORRECT;
   }
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Session time on a given server day from "HH:MM"                  |
//+------------------------------------------------------------------+
datetime At(datetime day, string hhmm)
{
   string parts[];
   if(StringSplit(hhmm, ':', parts) != 2)
      return day;
   return day + (int)StringToInteger(parts[0]) * 3600 + (int)StringToInteger(parts[1]) * 60;
}

datetime DayStart(datetime t)
{
   return t - (t % 86400);
}

//+------------------------------------------------------------------+
bool HasPosition()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
         PositionGetInteger(POSITION_MAGIC) == (long)InpMagic)
         return true;
   }
   return false;
}

// Close our positions that are past the session end or left over from a previous day
void ManageExits(datetime now, datetime day, datetime nyEnd)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol ||
         PositionGetInteger(POSITION_MAGIC) != (long)InpMagic)
         continue;
      datetime opened = (datetime)PositionGetInteger(POSITION_TIME);
      if(now >= nyEnd || opened < day)
         trade.PositionClose(ticket);
   }
}

// True if this EA already opened a trade today (survives terminal restarts)
bool TradedToday(datetime day, datetime now)
{
   if(!HistorySelect(day, now + 60))
      return false;
   for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
   {
      ulong deal = HistoryDealGetTicket(i);
      if(HistoryDealGetString(deal, DEAL_SYMBOL) == _Symbol &&
         HistoryDealGetInteger(deal, DEAL_MAGIC) == (long)InpMagic &&
         HistoryDealGetInteger(deal, DEAL_ENTRY) == DEAL_ENTRY_IN)
         return true;
   }
   return false;
}

//+------------------------------------------------------------------+
double NormalizeLots(double lots)
{
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minL = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxL = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   lots = MathFloor(lots / step) * step;
   if(lots < minL) return 0.0;   // risk too small for the minimum lot: skip rather than over-risk
   return MathMin(maxL, lots);
}

double LotsForRisk(double slDistance)
{
   if(InpRiskPercent <= 0 || slDistance <= 0)
      return NormalizeLots(InpFixedLots);
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE_LOSS);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tickValue <= 0 || tickSize <= 0)
      return 0.0;
   double lossPerLot = slDistance / tickSize * tickValue;
   return NormalizeLots(AccountInfoDouble(ACCOUNT_EQUITY) * InpRiskPercent / 100.0 / lossPerLot);
}

//+------------------------------------------------------------------+
void OnTick()
{
   datetime now   = TimeCurrent();
   datetime day   = DayStart(now);
   datetime lonT  = At(day, InpLondonStart);
   datetime nyT   = At(day, InpNYStart);
   datetime endT  = At(day, InpNYEnd);

   ManageExits(now, day, endT);

   if(now < nyT || now >= endT || now > nyT + InpEntryWindow * 60)
      return;
   if(HasPosition() || TradedToday(day, now))
      return;

   // London net move: open of first M5 bar at London start -> close of last M5 bar before NY start
   MqlRates r[];
   int n = CopyRates(_Symbol, PERIOD_M5, lonT, nyT - 1, r);
   if(n < 20)
      return;   // not enough London data (holiday or history still loading)
   double move  = r[n - 1].close - r[0].open;
   double scale = InpScaleByPrice ? r[0].open / InpRefPrice : 1.0;

   if(MathAbs(move) <= InpLondonMove * scale)
      return;
   if(InpMaxLondonMove > 0 && MathAbs(move) > InpMaxLondonMove * scale)
      return;
   if((InpSide == SIDE_LONG && move < 0) || (InpSide == SIDE_SHORT && move > 0))
      return;
   double slDist = InpStopLoss * scale;
   double tpDist = InpTakeProfit * scale;

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return;
   if(tick.ask - tick.bid > InpMaxSpread)
      return;   // retry on later ticks inside the entry window

   double lots = LotsForRisk(slDist);
   if(lots <= 0)
      return;

   // 0 = no stop / no target
   if(move > 0)
   {
      double sl = slDist > 0 ? NormalizeDouble(tick.ask - slDist, _Digits) : 0.0;
      double tp = tpDist > 0 ? NormalizeDouble(tick.ask + tpDist, _Digits) : 0.0;
      trade.Buy(lots, _Symbol, tick.ask, sl, tp, StringFormat("LDN +%.1f", move));
   }
   else
   {
      double sl = slDist > 0 ? NormalizeDouble(tick.bid + slDist, _Digits) : 0.0;
      double tp = tpDist > 0 ? NormalizeDouble(tick.bid - tpDist, _Digits) : 0.0;
      trade.Sell(lots, _Symbol, tick.bid, sl, tp, StringFormat("LDN %.1f", move));
   }
}
//+------------------------------------------------------------------+
