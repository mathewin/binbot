//+------------------------------------------------------------------+
//|                                              onecanal10_monkey.mq5 |
//|  Conta $10 | canal min 1 | max 50% margem | lote +0.01 a cada Nx  |
//+------------------------------------------------------------------+
#property copyright "Mathewin"
#property version   "1.21"
#property strict

#include <Trade\Trade.mqh>
CTrade trade;

#define CFG_VER 21

input group "=== Padrao (depois muda no painel) ==="
input int    InpChannelPts   = 1;        // Canal em pontos (min 1, sem teto)
input double InpLot          = 0.01;     // Lote inicial da fase
input double InpRiskPct      = 0.0;      // Risco % do saldo por ordem (0 = usa lote da fase)
input int    InpMaxOrders    = 0;        // Ignorado: max = 50% da margem do saldo
input int    InpScaleX       = 3;        // Lucro Nx pra subir lote (2,3,4,5...). Muda no painel com bot off
input double InpRewardRatio  = 0.0;      // RR: 0=off (fecha na reversao/trail)
input int    InpLockPts      = 0;        // Trava extra em pts (0 = off)
input int    InpTrendConfirm = 2;        // Canais pra confirmar tendencia (1 = off)
input double InpBasketDDPct  = 0.0;      // Teto DD do cesto % do saldo (0 = off). Em $10 use 0
input int    InpMaxSpreadPts = 30;       // Spread max pts (0 = off). Media XAU ~25, use 30

input group "=== Log CSV ==="
input bool   InpLogCsv       = true;     // Grava cada operacao encerrada em CSV (MQL5/Files)
input bool   InpCsvVirgula   = true;     // Numeros com virgula decimal (Excel pt-BR)

input group "=== Painel ==="
input bool   InpShowPanel    = true;     // Mostrar painel no grafico

input group "=== Execucao ==="
input ulong  InpMagicNumber  = 55510;
input int    InpSlippagePts  = 80;
input string InpTradeComment = "OneCanal10";

//====================================================================
double   g_step = 0;
double   g_origin = 0;
double   g_bottom = 0, g_top = 0;
bool     g_gridReady = false;
int      g_dir = 0;              // 1 compra, -1 venda, 0 idle
double   g_lastBreak = 0;        // ultimo nivel rompido nesta sequencia
int      g_channelPts = 1;
double   g_lot = 0.01;
double   g_riskPct = 0;
int      g_maxOrders = 0;
double   g_phaseStart = 0;
double   g_peakEq = 0;
bool     g_killed = false;
int      g_scaleX = 3;
double   g_rr = 0;
int      g_lockPts = 0;
int      g_trendNeed = 2;
int      g_trend = 0;
int      g_streakDir = 0;
int      g_streak = 0;
int      g_pendingDir = 0;
double   g_pendingLevel = 0;
bool     g_botOn = true;
string   g_msg = "";
datetime g_retryAt = 0;
double   g_basketDDPct = 0;
int      g_maxSpreadPts = 0;
bool     g_openedThisTick = false;
ulong    g_csvDeals[];

#define PNL_PREFIX "OneCanal10Pnl_"
#define PANEL_W 330
#define PANEL_TITLE_H 26
int  g_panelX = 10, g_panelY = 20;
int  g_panelLines = 0, g_panelH = 0;
bool g_dragging = false, g_prevDown = false, g_scrollWas = true;
int  g_dragDX = 0, g_dragDY = 0;

const string OBJ_TOP  = "OneCanal10_CanalTopo";
const string OBJ_BOT  = "OneCanal10_CanalFundo";
const string OBJ_ZERO = "OneCanal10_PontoZero";
const string OBJ_STOP = "OneCanal10_StopLinha";
const string OBJ_TP   = "OneCanal10_AlvoLinha";
const string OBJ_LOCK = "OneCanal10_TravaLinha";

struct SStats
{
   int    trades, wins, losses, be, tps, sls, prots, others;
   double net, grossWin, grossLoss, bestTrade, worstTrade, points;
   double netTP, netSL, netProt, netOther;
};
SStats g_all, g_today;
datetime g_statsFrom = 0;
datetime g_resetArm  = 0;
datetime g_closeArm  = 0;

//====================================================================
string GVKey()  { return "OneCanal10_ON_" + _Symbol + "_" + IntegerToString((long)InpMagicNumber); }
string CfgKey(string k) { return "OneCanal10_" + k + "_" + _Symbol + "_" + IntegerToString((long)InpMagicNumber); }
string PosKey(string axis) { return "OneCanal10_P" + axis + "_" + _Symbol + "_" + IntegerToString((long)InpMagicNumber); }
string BtnName()     { return PNL_PREFIX + "BTN"; }
string EdCanal()     { return PNL_PREFIX + "EDCAN"; }
string EdLot()       { return PNL_PREFIX + "EDLOT"; }
string EdRisk()      { return PNL_PREFIX + "EDRSK"; }
string EdMax()       { return PNL_PREFIX + "EDMAX"; }
string EdRR()        { return PNL_PREFIX + "EDRR"; }
string EdLock()      { return PNL_PREFIX + "EDLCK"; }
string EdTrend()     { return PNL_PREFIX + "EDTRN"; }
string EdDD()        { return PNL_PREFIX + "EDDD"; }
string EdSpr()       { return PNL_PREFIX + "EDSPR"; }
string EdScale()     { return PNL_PREFIX + "EDSCX"; }
string BtnResetName(){ return PNL_PREFIX + "BTNRST"; }
string BtnCloseName(){ return PNL_PREFIX + "BTNCLOSE"; }
string StatsKey()    { return "OneCanal10_STATS_" + _Symbol + "_" + IntegerToString((long)InpMagicNumber); }
string CsvFile()     { return "OneCanal10_" + _Symbol + "_" + IntegerToString((long)InpMagicNumber) + ".csv"; }

int CsvOpenFlags()
{
   return FILE_READ | FILE_WRITE | FILE_ANSI | FILE_TXT | FILE_COMMON | FILE_SHARE_READ | FILE_SHARE_WRITE;
}

double PointSize()
{
   double pt = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   return (pt > 0) ? pt : 0.00001;
}

int StepPtsLocked()
{
   double pt = PointSize();
   if(pt <= 0 || g_step <= 0) return g_channelPts;
   return (int)MathRound(g_step / pt);
}

double ComputeStep()
{
   double pt = PointSize();
   int spr = SpreadPtsNow();
   if(spr < 0) spr = 0;
    int pts = g_channelPts + spr;
    if(pts < 1) pts = 1;
    return pts * pt;
}

double SpreadPrice()
{
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if(ask <= 0 || bid <= 0) return 0;
   return ask - bid;
}

int SpreadPtsNow()
{
   double pt = PointSize();
   if(pt <= 0) return 0;
   return (int)MathRound(SpreadPrice() / pt);
}

int MaxSpreadAllowed()
{
   return g_maxSpreadPts;
}

double MinStopRoom()
{
   double pt = PointSize();
   if(pt <= 0) pt = 0.00001;
   double stops  = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * pt;
   double freeze = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL) * pt;
   double spr    = SpreadPrice();
   return MathMax(pt, MathMax(stops, MathMax(freeze, spr))) + pt;
}

void SetupFilling()
{
   long modes = SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((modes & SYMBOL_FILLING_IOC) == SYMBOL_FILLING_IOC)
      trade.SetTypeFilling(ORDER_FILLING_IOC);
   else if((modes & SYMBOL_FILLING_FOK) == SYMBOL_FILLING_FOK)
      trade.SetTypeFilling(ORDER_FILLING_FOK);
   else
      trade.SetTypeFilling(ORDER_FILLING_RETURN);
}

string FmtRR()
{
   if(g_rr <= 0) return "0";
   double nearest = MathRound(g_rr);
   if(nearest >= 1 && MathAbs(g_rr - nearest) < 0.0005)
      return DoubleToString(nearest, 0) + ".1";
   return DoubleToString(g_rr, 2);
}

double ParseRRText(const string raw, bool &ok)
{
   ok = false;
   string s = raw;
   StringReplace(s, " ", "");
   StringReplace(s, ",", ".");
   if(s == "")
   {
      ok = true;
      return 0;
   }

   int pColon = StringFind(s, ":");
   int pSlash = StringFind(s, "/");
   int p = (pColon >= 0) ? pColon : pSlash;
   if(p > 0)
   {
      double reward = StringToDouble(StringSubstr(s, 0, p));
      double risk   = StringToDouble(StringSubstr(s, p + 1));
      if(reward < 0 || risk <= 0 || reward / risk > 50) return 0;
      ok = true;
      return reward / risk;
   }

   double val = StringToDouble(s);
   if(val < 0 || val > 50) return 0;
   ok = true;
   if(val == 0) return 0;
   double whole = MathFloor(val + 1e-8);
   double frac  = val - whole;
   if(whole >= 1 && MathAbs(frac - 0.1) < 0.0005)
      return whole;
   return val;
}

void SaveCfg()
{
   GlobalVariableSet(CfgKey("CH"), g_channelPts);
   GlobalVariableSet(CfgKey("LOT"), g_lot);
   GlobalVariableSet(CfgKey("RISK"), g_riskPct);
   GlobalVariableSet(CfgKey("MAX"), g_maxOrders);
    GlobalVariableSet(CfgKey("RR"), g_rr);
    GlobalVariableSet(CfgKey("LOCK"), g_lockPts);
    GlobalVariableSet(CfgKey("TRN"), g_trendNeed);
    GlobalVariableSet(CfgKey("TRD"), g_trend);
    GlobalVariableSet(CfgKey("STD"), g_streakDir);
    GlobalVariableSet(CfgKey("STK"), g_streak);
    GlobalVariableSet(CfgKey("ORG"), g_origin);
    GlobalVariableSet(CfgKey("LB"), g_lastBreak);
    GlobalVariableSet(CfgKey("TOP"), g_top);
    GlobalVariableSet(CfgKey("BOT"), g_bottom);
     GlobalVariableSet(CfgKey("GRD"), g_gridReady ? 1 : 0);
     GlobalVariableSet(CfgKey("STEP"), g_step);
     GlobalVariableSet(CfgKey("DD"), g_basketDDPct);
     GlobalVariableSet(CfgKey("SPR"), g_maxSpreadPts);
     GlobalVariableSet(CfgKey("PHB"), g_phaseStart);
     GlobalVariableSet(CfgKey("PEK"), g_peakEq);
     GlobalVariableSet(CfgKey("KIL"), g_killed ? 1 : 0);
     GlobalVariableSet(CfgKey("SCX"), g_scaleX);
     GlobalVariableSet(CfgKey("VER"), CFG_VER);
}

void LoadCfg()
{
    g_channelPts = InpChannelPts;
    g_lot        = InpLot;
    g_riskPct    = InpRiskPct;
    g_maxOrders  = InpMaxOrders;
    g_rr         = InpRewardRatio;
    g_lockPts    = InpLockPts;
     g_trendNeed    = InpTrendConfirm;
     g_basketDDPct  = InpBasketDDPct;
     g_maxSpreadPts = InpMaxSpreadPts;
     g_scaleX       = InpScaleX;
     bool tester = (bool)MQLInfoInteger(MQL_TESTER);
     int savedVer = (!tester && GlobalVariableCheck(CfgKey("VER"))) ? (int)GlobalVariableGet(CfgKey("VER")) : 0;
     if(!tester && savedVer == CFG_VER)
     {
        if(GlobalVariableCheck(CfgKey("CH")))   g_channelPts = (int)GlobalVariableGet(CfgKey("CH"));
        if(GlobalVariableCheck(CfgKey("LOT")))  g_lot        = GlobalVariableGet(CfgKey("LOT"));
        if(GlobalVariableCheck(CfgKey("RISK"))) g_riskPct    = GlobalVariableGet(CfgKey("RISK"));
        if(GlobalVariableCheck(CfgKey("MAX")))  g_maxOrders  = (int)GlobalVariableGet(CfgKey("MAX"));
        if(GlobalVariableCheck(CfgKey("RR")))   g_rr         = GlobalVariableGet(CfgKey("RR"));
        if(GlobalVariableCheck(CfgKey("LOCK"))) g_lockPts    = (int)GlobalVariableGet(CfgKey("LOCK"));
        if(GlobalVariableCheck(CfgKey("TRN")))  g_trendNeed  = (int)GlobalVariableGet(CfgKey("TRN"));
        if(GlobalVariableCheck(CfgKey("TRD")))  g_trend      = (int)GlobalVariableGet(CfgKey("TRD"));
        if(GlobalVariableCheck(CfgKey("STD")))  g_streakDir  = (int)GlobalVariableGet(CfgKey("STD"));
        if(GlobalVariableCheck(CfgKey("STK")))  g_streak     = (int)GlobalVariableGet(CfgKey("STK"));
        if(GlobalVariableCheck(CfgKey("ORG")))  g_origin     = GlobalVariableGet(CfgKey("ORG"));
        if(GlobalVariableCheck(CfgKey("LB")))   g_lastBreak  = GlobalVariableGet(CfgKey("LB"));
        if(GlobalVariableCheck(CfgKey("TOP")))  g_top        = GlobalVariableGet(CfgKey("TOP"));
        if(GlobalVariableCheck(CfgKey("BOT")))  g_bottom     = GlobalVariableGet(CfgKey("BOT"));
        if(GlobalVariableCheck(CfgKey("GRD")))  g_gridReady  = (GlobalVariableGet(CfgKey("GRD")) != 0);
        if(GlobalVariableCheck(CfgKey("STEP"))) g_step       = GlobalVariableGet(CfgKey("STEP"));
        if(GlobalVariableCheck(CfgKey("DD")))   g_basketDDPct = GlobalVariableGet(CfgKey("DD"));
        if(GlobalVariableCheck(CfgKey("SPR")))  g_maxSpreadPts = (int)GlobalVariableGet(CfgKey("SPR"));
        if(GlobalVariableCheck(CfgKey("PHB")))  g_phaseStart  = GlobalVariableGet(CfgKey("PHB"));
        if(GlobalVariableCheck(CfgKey("PEK")))  g_peakEq      = GlobalVariableGet(CfgKey("PEK"));
        if(GlobalVariableCheck(CfgKey("KIL")))  g_killed      = (GlobalVariableGet(CfgKey("KIL")) != 0);
        if(GlobalVariableCheck(CfgKey("SCX")))  g_scaleX      = (int)GlobalVariableGet(CfgKey("SCX"));
     }
     if(g_channelPts < 1) g_channelPts = 1;
     if(g_lot <= 0) g_lot = InpLot;
     if(g_riskPct < 0) g_riskPct = 0;
     if(g_maxOrders < 0) g_maxOrders = 0;
     if(g_phaseStart < 0) g_phaseStart = 0;
     if(g_rr < 0) g_rr = 0;
     if(g_rr > 0)
     {
        double whole = MathFloor(g_rr + 1e-8);
        double frac  = g_rr - whole;
        if(whole >= 1 && MathAbs(frac - 0.1) < 0.0005)
           g_rr = whole;
     }
     if(g_lockPts < 0) g_lockPts = 0;
     if(g_trendNeed < 1) g_trendNeed = 1;
     if(g_basketDDPct < 0) g_basketDDPct = 0;
     if(g_maxSpreadPts < 0) g_maxSpreadPts = 0;
     if(g_scaleX < 1) g_scaleX = 1;
     if(g_trend != 1 && g_trend != -1) g_trend = 0;
     if(g_streakDir != 1 && g_streakDir != -1) g_streakDir = 0;
     if(g_streak < 0) g_streak = 0;
     if(g_step <= 0 || !g_gridReady)
        g_step = ComputeStep();
     if(!tester) SaveCfg();
}

int CountMine()
{
   int n = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != (long)InpMagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      n++;
   }
   return n;
}

int DirMine()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != (long)InpMagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      return (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? 1 : -1;
   }
   return 0;
}

double OpenPnl()
{
   double v = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != (long)InpMagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      v += PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
   }
   return v;
}

double PosPts(const bool buy, const double open)
{
   double pt = PointSize();
   if(pt <= 0 || open <= 0) return 0;
   double px = buy ? SymbolInfoDouble(_Symbol, SYMBOL_BID) : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   return (buy ? (px - open) : (open - px)) / pt;
}

double OpenPts()
{
   double v = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != (long)InpMagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      bool buy = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
      v += PosPts(buy, PositionGetDouble(POSITION_PRICE_OPEN));
   }
   return v;
}

double NormalizeLot(double lot)
{
   double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(lotStep <= 0) lotStep = minLot;
   if(minLot <= 0) return 0;
   lot = MathFloor(lot / lotStep + 1e-8) * lotStep;
   if(lot < minLot) lot = minLot;
   if(lot > maxLot) lot = maxLot;
   return NormalizeDouble(lot, 2);
}

double MarginPerLot(int direction)
{
   double lot = NormalizeLot(g_lot);
   if(lot <= 0) lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   if(lot <= 0) return 0;
   ENUM_ORDER_TYPE ot = (direction == 1) ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   double price = (direction == 1)
                  ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                  : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if(price <= 0) price = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double margin = 0;
   if(!OrderCalcMargin(ot, _Symbol, lot, price, margin) || margin <= 0)
      return 0;
   return margin;
}

int AffordableOrders()
{
   double per = MarginPerLot(g_dir != 0 ? g_dir : 1);
   if(per <= 0) return 1;
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(equity <= 0) equity = AccountInfoDouble(ACCOUNT_BALANCE);
   int n = (int)MathFloor(equity / per);
   if(n < 1) n = 1;
   return n;
}

int MaxOrdersCap()
{
   int fit = AffordableOrders();
   if(fit <= 1) return 1;
   int half = fit / 2;
   if(half < 1) half = 1;
   return half;
}

bool AtMaxOrders()
{
   return (CountMine() >= MaxOrdersCap());
}

bool MarginCanOpen(double lot, int direction)
{
   if(lot <= 0) return false;
   if(AtMaxOrders()) return false;
   ENUM_ORDER_TYPE ot = (direction == 1) ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   double price = (direction == 1)
                  ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                  : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double margin = 0;
   if(!OrderCalcMargin(ot, _Symbol, lot, price, margin) || margin <= 0)
      return true;
   double free = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   return (free >= margin);
}

string MaxOrdersText()
{
   return IntegerToString(MaxOrdersCap()) + "/50%";
}

void EnsurePhaseStart()
{
   if(g_phaseStart > 0) return;
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   if(bal <= 0) return;
   g_phaseStart = bal;
   if(g_peakEq < bal) g_peakEq = bal;
   SaveCfg();
}

double EquityNow()
{
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   if(eq <= 0) eq = AccountInfoDouble(ACCOUNT_BALANCE);
   return eq;
}

void KillBot(const string why)
{
   if(g_killed && !g_botOn) return;
   g_killed = true;
   g_botOn = false;
   g_pendingDir = 0;
   GlobalVariableSet(GVKey(), 0);
   CloseAllMine();
   g_msg = why;
   SaveCfg();
   Print(why);
}

void CheckAccountStop()
{
   double eq = EquityNow();
   if(eq <= 0) return;
   if(eq > g_peakEq)
   {
      g_peakEq = eq;
      SaveCfg();
   }
   if(g_killed) return;
   if(g_peakEq <= 0) return;
   if(eq > g_peakEq * 0.50) return;
   KillBot("STOP 50%: equity " + DoubleToString(eq, 2) +
           " <= metade do pico " + DoubleToString(g_peakEq, 2) + ". Bot desligado.");
}

double ScaleTarget()
{
   int n = (g_scaleX < 1) ? 1 : g_scaleX;
   return g_phaseStart * (1.0 + (double)n);
}

void ScaleLotByProfit()
{
   EnsurePhaseStart();
   if(g_phaseStart <= 0) return;
   if(CountMine() > 0) return;
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   if(bal < ScaleTarget()) return;
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0) step = 0.01;
   double next = NormalizeLot(g_lot + step);
   if(next <= g_lot) return;
   PrintFormat("OneCanal10 lote %.2f -> %.2f | fase $%.2f lucro %dx saldo $%.2f",
               g_lot, next, g_phaseStart, g_scaleX, bal);
   g_lot = next;
   g_phaseStart = bal;
   SaveCfg();
}

double LotForStop(double stopDistPrice)
{
   if(g_riskPct <= 0)
      return NormalizeLot(g_lot);

   if(stopDistPrice <= 0) return 0;
   double tv = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double ts = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tv <= 0 || ts <= 0) return NormalizeLot(g_lot);

   double moneyPerLot = (stopDistPrice / ts) * tv;
   if(moneyPerLot <= 0) return 0;
   double riskMoney = AccountInfoDouble(ACCOUNT_BALANCE) * (g_riskPct / 100.0);
   return NormalizeLot(riskMoney / moneyPerLot);
}

void Reject(string msg)
{
   if(msg != g_msg) Print(msg);
   g_msg = msg;
   g_retryAt = TimeCurrent() + 2;
}

bool BasketDDHit()
{
   if(g_basketDDPct <= 0) return false;
   int n = CountMine();
   if(n <= 0) return false;
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   if(bal <= 0) return false;
   double pnl = OpenPnl();
   double cap = -(bal * (g_basketDDPct / 100.0));
   return (pnl <= cap);
}

string Nm(double v, int digits)
{
   string t = DoubleToString(v, digits);
   if(InpCsvVirgula) StringReplace(t, ".", ",");
   return t;
}

bool CsvAlreadyLogged(ulong deal)
{
   int n = ArraySize(g_csvDeals);
   for(int i = 0; i < n; i++)
      if(g_csvDeals[i] == deal) return true;
   return false;
}

void CsvMarkLogged(ulong deal)
{
   int n = ArraySize(g_csvDeals);
   ArrayResize(g_csvDeals, n + 1);
   g_csvDeals[n] = deal;
}

void LogTradeClose(ulong deal)
{
   if(!InpLogCsv) return;
   if(deal == 0) return;
   if(CsvAlreadyLogged(deal)) return;
   if(!HistoryDealSelect(deal)) return;

   long     reason   = HistoryDealGetInteger(deal, DEAL_REASON);
   double   outPrice = HistoryDealGetDouble(deal, DEAL_PRICE);
   datetime outTime  = (datetime)HistoryDealGetInteger(deal, DEAL_TIME);
   long     outType  = HistoryDealGetInteger(deal, DEAL_TYPE);
   ulong    posId    = (ulong)HistoryDealGetInteger(deal, DEAL_POSITION_ID);
   double   pt       = PointSize();
   int      dir      = (outType == DEAL_TYPE_SELL) ? 1 : -1;

   double   net = 0, inPrice = 0, lot = 0, commSum = 0;
   datetime inTime = 0;
   if(HistorySelectByPosition(posId))
   {
      int m = HistoryDealsTotal();
      for(int j = 0; j < m; j++)
      {
         ulong d = HistoryDealGetTicket(j);
         if(d == 0) continue;
         net += HistoryDealGetDouble(d, DEAL_PROFIT) + HistoryDealGetDouble(d, DEAL_SWAP)
              + HistoryDealGetDouble(d, DEAL_COMMISSION) + HistoryDealGetDouble(d, DEAL_FEE);
         commSum += HistoryDealGetDouble(d, DEAL_COMMISSION) + HistoryDealGetDouble(d, DEAL_FEE);
         if(HistoryDealGetInteger(d, DEAL_ENTRY) == DEAL_ENTRY_IN)
         {
            inPrice = HistoryDealGetDouble(d, DEAL_PRICE);
            inTime  = (datetime)HistoryDealGetInteger(d, DEAL_TIME);
            lot     = HistoryDealGetDouble(d, DEAL_VOLUME);
         }
      }
   }

   double pts = 0;
   if(pt > 0 && inPrice > 0)
      pts = ((dir == 1) ? (outPrice - inPrice) : (inPrice - outPrice)) / pt;

   string kind = "OUTRO";
   if(reason == DEAL_REASON_TP)      kind = "ALVO";
   else if(reason == DEAL_REASON_SL) kind = (pts > 0) ? "PROTEGIDO" : "STOP";
   else if(reason == DEAL_REASON_EXPERT) kind = "REVERSAO";

   MqlDateTime dtIn;
   TimeToStruct(inTime, dtIn);
   int spreadPts = SpreadPtsNow();

   string line =
      TimeToString(inTime, TIME_DATE | TIME_SECONDS) + ";" +
      TimeToString(outTime, TIME_DATE | TIME_SECONDS) + ";" +
      _Symbol + ";" +
      (dir == 1 ? "COMPRA" : "VENDA") + ";" +
      kind + ";" +
      IntegerToString(g_channelPts) + ";" +
      IntegerToString(spreadPts) + ";" +
      Nm(inPrice, _Digits) + ";" +
      Nm(outPrice, _Digits) + ";" +
      Nm(pts, 0) + ";" +
      Nm(net, 2) + ";" +
      Nm(commSum, 2) + ";" +
      Nm(lot, 2) + ";" +
      IntegerToString((long)(outTime - inTime)) + ";" +
      IntegerToString(g_trendNeed) + ";" +
      (g_trend == 1 ? "ALTA" : (g_trend == -1 ? "BAIXA" : "-")) + ";" +
      IntegerToString(dtIn.hour) + ";" +
      IntegerToString(dtIn.day_of_week);

   string header = "data_abertura;data_fechamento;simbolo;sentido;saida;canal_pts;spread_pts;" +
                   "entrada;preco_saida;resultado_pts;lucro_liquido;comissao_paga;lote;duracao_seg;" +
                   "confirma_n;tendencia;hora;dia_semana";

   int h = FileOpen(CsvFile(), CsvOpenFlags());
   if(h == INVALID_HANDLE)
      PrintFormat("CSV indisponivel (erro %d). Linha: %s", GetLastError(), line);
   else
   {
      if(FileSize(h) == 0)
         FileWriteString(h, header + "\r\n");
      FileSeek(h, 0, SEEK_END);
      FileWriteString(h, line + "\r\n");
      FileFlush(h);
      FileClose(h);
      CsvMarkLogged(deal);
      PrintFormat("CSV gravado: Common\\Files\\%s | %s %s %s",
                  CsvFile(), (dir == 1 ? "COMPRA" : "VENDA"), kind, Nm(net, 2));
   }
}

void FlushCsvFromHistory()
{
   if(!InpLogCsv) return;
   if(!HistorySelect(0, TimeCurrent() + 86400)) return;
   ulong pending[];
   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong t = HistoryDealGetTicket(i);
      if(t == 0) continue;
      if(HistoryDealGetInteger(t, DEAL_MAGIC) != (long)InpMagicNumber) continue;
      if(HistoryDealGetString(t, DEAL_SYMBOL) != _Symbol) continue;
      if(HistoryDealGetInteger(t, DEAL_ENTRY) != DEAL_ENTRY_OUT) continue;
      if(CsvAlreadyLogged(t)) continue;
      int n = ArraySize(pending);
      ArrayResize(pending, n + 1);
      pending[n] = t;
   }
   int nPend = ArraySize(pending);
   for(int k = 0; k < nPend; k++)
      LogTradeClose(pending[k]);
}

//====================================================================
void MarkChannelAtPrice()
{
   if(g_gridReady && g_origin > 0 && g_step > 0)
   {
      DrawChannel();
      return;
   }
    double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
    double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
    if(bid <= 0 || ask <= 0) return;
    double c = ComputeStep();
    if(c <= 0) return;

    double mid = (bid + ask) / 2.0;
    g_step      = c;
    g_origin    = NormalizeDouble(mid, _Digits);
    g_bottom    = NormalizeDouble(g_origin - c, _Digits);
    g_top       = NormalizeDouble(g_origin + c, _Digits);
    g_gridReady = true;
    g_dir = 0;
    g_lastBreak = 0;
    SaveCfg();
    DrawChannel();
    PrintFormat("Ponto 0: %s | canal %s / %s | miolo=%d passo=%d pts (spread %d)",
                DoubleToString(g_origin, _Digits),
                DoubleToString(g_bottom, _Digits),
                DoubleToString(g_top, _Digits),
                g_channelPts, StepPtsLocked(), SpreadPtsNow());
}

void DrawHLine(string name, double price, color cor, int style, int width, string tip)
{
   price = NormalizeDouble(price, _Digits);
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_HLINE, 0, 0, price);
   ObjectSetDouble(0, name, OBJPROP_PRICE, price);
   ObjectSetInteger(0, name, OBJPROP_COLOR, cor);
   ObjectSetInteger(0, name, OBJPROP_STYLE, style);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, width);
   ObjectSetInteger(0, name, OBJPROP_BACK, false);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   ObjectSetString(0, name, OBJPROP_TOOLTIP, tip);
}

void ClearChartLines()
{
   ObjectDelete(0, OBJ_TOP);
   ObjectDelete(0, OBJ_BOT);
   ObjectDelete(0, OBJ_ZERO);
   ObjectDelete(0, OBJ_STOP);
   ObjectDelete(0, OBJ_TP);
   ObjectDelete(0, OBJ_LOCK);
}

void DrawPosLevels()
{
   double tpPx = 0, lockPx = 0;
   bool hasTp = false, hasLock = false;
   datetime newest = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != (long)InpMagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      datetime t = (datetime)PositionGetInteger(POSITION_TIME);
      double tp = PositionGetDouble(POSITION_TP);
      double open = PositionGetDouble(POSITION_PRICE_OPEN);
      if(tp > 0 && (!hasTp || t >= newest))
      {
         tpPx = tp;
         hasTp = true;
         newest = t;
      }
      if(g_lockPts > 0)
      {
         lockPx = open;
         hasLock = true;
      }
   }
   if(hasTp)
      DrawHLine(OBJ_TP, tpPx, clrGold, STYLE_DOT, 2, "ALVO / TP");
   else
      ObjectDelete(0, OBJ_TP);
   if(hasLock)
      DrawHLine(OBJ_LOCK, lockPx, clrLime, STYLE_DASHDOTDOT, 1, "TRAVA (entrada / breakeven)");
   else
      ObjectDelete(0, OBJ_LOCK);
}

void DrawChannel()
{
   if(!g_gridReady)
   {
      ClearChartLines();
      return;
   }
   DrawHLine(OBJ_TOP, g_top, clrDeepSkyBlue, STYLE_SOLID, 2, "CANAL TOPO (rompimento de COMPRA)");
   DrawHLine(OBJ_BOT, g_bottom, clrOrange, STYLE_SOLID, 2, "CANAL FUNDO (rompimento de VENDA)");
   if(g_origin > 0)
      DrawHLine(OBJ_ZERO, g_origin, clrSilver, STYLE_DOT, 1, "PONTO 0");
    ObjectDelete(0, OBJ_STOP);
    DrawPosLevels();
   ChartRedraw(0);
}

void MoveChannel(int direction, double breakLevel)
{
    g_lastBreak = breakLevel;
    if(direction == 1)
    {
       g_bottom = NormalizeDouble(breakLevel, _Digits);
       g_top    = NormalizeDouble(breakLevel + g_step, _Digits);
    }
    else
    {
       g_top    = NormalizeDouble(breakLevel, _Digits);
       g_bottom = NormalizeDouble(breakLevel - g_step, _Digits);
    }
    DrawChannel();
}

void SetStreak(int dir, int n)
{
    if(dir != 1 && dir != -1) return;
    if(n < 1) n = 1;
    bool same = (g_streakDir == dir && g_streak == n && g_trend != 0);
    g_streakDir = dir;
    g_streak    = n;
    if(g_trend == 0)
       g_trend = dir;
    else if(g_streak >= g_trendNeed)
       g_trend = dir;
    if(!same) SaveCfg();
}

void RegisterBreak(int direction)
{
    int n = (g_streakDir == direction) ? (g_streak + 1) : 1;
    SetStreak(direction, n);
}

bool AllowEntry(int direction)
{
    if(g_trendNeed <= 1) return true;
    if(g_trend == 0) return true;
    return (g_trend == direction);
}

string WaitTrendMsg(int direction)
{
    return "tendencia " + (g_trend == 1 ? "ALTA" : (g_trend == -1 ? "BAIXA" : "-")) +
           " | reversao " + IntegerToString(g_streak) + "/" + IntegerToString(g_trendNeed) +
           " " + (direction == 1 ? "alta" : "baixa");
}

string TrendText()
{
    if(g_trendNeed <= 1) return "Tendencia: off (entra nos 2 lados)";
    string t = (g_trend == 1 ? "ALTA" : (g_trend == -1 ? "BAIXA" : "aguardando 1o rompimento"));
    if(g_trend == 0)
       return "Tendencia: " + t;
    if(g_streakDir == g_trend)
       return "Tendencia: " + t + " | opera so " + (g_trend == 1 ? "COMPRA" : "VENDA");
    return "Tendencia: " + t + " | pullback " + IntegerToString(g_streak) + "/" +
           IntegerToString(g_trendNeed) + " p/ reverter";
}

bool StopsOk(const bool buy, const double sl, const double tp)
{
    double pt = PointSize();
    double minDist = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * pt;
    double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
    double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
    if(sl != 0)
    {
       double slRoom = buy ? (bid - sl) : (sl - ask);
       if(slRoom < minDist) return false;
    }
    if(tp != 0)
    {
       double tpRoom = buy ? (tp - bid) : (ask - tp);
       if(tpRoom < minDist) return false;
    }
    return true;
}

bool IsProtected(const bool buy, const double open, const double sl)
{
   if(sl == 0 || g_step <= 0) return false;
   double slack = g_step * 0.25;
   if(buy)  return (sl >= open - slack);
   return (sl <= open + slack);
}

bool LastOrderProtected()
{
   datetime newest = 0;
   bool have = false, buy = false;
   double open = 0, sl = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != (long)InpMagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      datetime t = (datetime)PositionGetInteger(POSITION_TIME);
      if(!have || t >= newest)
      {
         have = true;
         newest = t;
         buy  = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
         open = PositionGetDouble(POSITION_PRICE_OPEN);
         sl   = PositionGetDouble(POSITION_SL);
      }
   }
   if(!have) return true;
   return IsProtected(buy, open, sl);
}

void TrailStopsOneStep(int direction)
{
   if(g_step <= 0) return;
   double pt = PointSize();
   if(pt <= 0) return;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != (long)InpMagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      bool buy = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
      if(direction == 1 && !buy) continue;
      if(direction == -1 && buy) continue;

      double open = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl   = PositionGetDouble(POSITION_SL);
      double tp   = PositionGetDouble(POSITION_TP);
      double nextSl = buy
                      ? NormalizeDouble(sl + g_step, _Digits)
                      : NormalizeDouble(sl - g_step, _Digits);
      if(sl == 0)
         nextSl = buy
                  ? NormalizeDouble(open, _Digits)
                  : NormalizeDouble(open, _Digits);

      if(buy)
      {
         if(nextSl <= sl + pt / 2.0) continue;
      }
      else
      {
         if(sl != 0 && nextSl >= sl - pt / 2.0) continue;
      }
      if(!StopsOk(buy, nextSl, tp)) continue;
      if(!trade.PositionModify(tk, nextSl, tp))
         PrintFormat("Falha no trail ticket %I64u: %s", tk, trade.ResultRetcodeDescription());
      else
         PrintFormat("Trail +1 passo ticket %I64u SL %s -> %s",
                     tk, DoubleToString(sl, _Digits), DoubleToString(nextSl, _Digits));
   }
}

void ProtectPositions()
{
    if(g_step <= 0) return;
    double pt = PointSize();
    if(pt <= 0) return;
    if(g_lockPts <= 0) return;

    for(int i = PositionsTotal() - 1; i >= 0; i--)
    {
       ulong tk = PositionGetTicket(i);
       if(tk == 0) continue;
       if(PositionGetInteger(POSITION_MAGIC) != (long)InpMagicNumber) continue;
       if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

       bool buy = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
       double open = PositionGetDouble(POSITION_PRICE_OPEN);
       double sl   = PositionGetDouble(POSITION_SL);
       double tp   = PositionGetDouble(POSITION_TP);
       double nextSl = sl;

       if(PosPts(buy, open) >= g_lockPts)
       {
          double lockSl = NormalizeDouble(open, _Digits);
          if(buy)
          {
             if(sl == 0 || lockSl > sl + pt / 2.0)
                nextSl = lockSl;
          }
          else
          {
             if(sl == 0 || lockSl < sl - pt / 2.0)
                nextSl = lockSl;
          }
       }

       if(MathAbs(nextSl - sl) < pt / 2.0) continue;
       if(!StopsOk(buy, nextSl, tp)) continue;
       if(!trade.PositionModify(tk, nextSl, tp))
          PrintFormat("Falha ao ajustar SL ticket %I64u: %s", tk, trade.ResultRetcodeDescription());
       else
          PrintFormat("Trava ativada ticket %I64u em %s", tk, DoubleToString(open, _Digits));
    }
}

bool HasOrderOnChannel(int direction, double breakLevel)
{
    if(g_step <= 0) return false;
    double pt = PointSize();
    double key = NormalizeDouble(breakLevel, _Digits);
    for(int i = PositionsTotal() - 1; i >= 0; i--)
    {
       ulong tk = PositionGetTicket(i);
       if(tk == 0) continue;
       if(!PositionSelectByTicket(tk)) continue;
       if(PositionGetInteger(POSITION_MAGIC) != (long)InpMagicNumber) continue;
       if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
       bool buy = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
       if(direction == 1 && !buy) continue;
       if(direction == -1 && buy) continue;
       double open = PositionGetDouble(POSITION_PRICE_OPEN);
       if(direction == 1)
       {
          if(open >= key - pt && open < key + g_step - pt / 2.0)
             return true;
       }
       else
       {
          if(open <= key + pt && open > key - g_step + pt / 2.0)
             return true;
       }
    }
    return false;
}

bool OpenOne(int direction, double breakLevel)
{
   if(g_openedThisTick) return false;
   if(TimeCurrent() < g_retryAt) return false;
   if(CountAgainst(direction) > 0) return false;
   if(BasketDDHit())
   {
      Reject("SEM ENTRADA: teto DD do cesto");
      return false;
   }
   if(HasOrderOnChannel(direction, breakLevel))
   {
      g_msg = "canal ja tem ordem";
      return false;
   }

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double pt  = PointSize();
   double minDist = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * pt;
    int sprPts = SpreadPtsNow();
    int maxSpr = MaxSpreadAllowed();
    if(maxSpr > 0 && sprPts > maxSpr)
    {
       Reject("SEM ENTRADA: spread " + IntegerToString(sprPts) + " > " + IntegerToString(maxSpr) + " pts");
       return false;
    }
    double entry = (direction == 1) ? ask : bid;
    double sl = (direction == 1)
                ? NormalizeDouble(breakLevel - g_step, _Digits)
                : NormalizeDouble(breakLevel + g_step, _Digits);
    double slRoom = (direction == 1) ? bid - sl : sl - ask;
    if(slRoom < minDist)
    {
       Reject("SEM ENTRADA: stop perto demais (" + DoubleToString(slRoom / pt, 0) + " pts)");
       return false;
    }
    double risk = (direction == 1) ? (entry - sl) : (sl - entry);
    if(risk <= 0)
    {
       Reject("SEM ENTRADA: stop invalido");
       return false;
    }
    double tp = 0;
    if(g_rr > 0)
       tp = (direction == 1)
            ? NormalizeDouble(entry + g_rr * risk, _Digits)
            : NormalizeDouble(entry - g_rr * risk, _Digits);

   slRoom = (direction == 1) ? bid - sl : sl - ask;
   if(slRoom <= 0 || slRoom < minDist)
   {
      Reject("SEM ENTRADA: stop perto demais (" + DoubleToString(slRoom / pt, 0) + " pts)");
      return false;
   }
   if(tp != 0)
   {
      double tpRoom = (direction == 1) ? tp - bid : ask - tp;
      if(tpRoom < minDist)
      {
         Reject("SEM ENTRADA: alvo perto demais");
         return false;
      }
   }

   double lot = LotForStop(risk);
   if(lot <= 0)
   {
      Reject("SEM ENTRADA: lote/risco invalido");
      return false;
   }
   if(!MarginCanOpen(lot, direction))
   {
      Reject("SEM ENTRADA: margem insuficiente");
      return false;
   }

    string cmt = InpTradeComment + " C=" + IntegerToString(g_channelPts) + " P=" + IntegerToString(StepPtsLocked());
   bool ok = (direction == 1)
             ? trade.Buy(lot, _Symbol, 0, sl, tp, cmt)
             : trade.Sell(lot, _Symbol, 0, sl, tp, cmt);
   if(!ok)
   {
      Reject("ERRO ORDEM: " + trade.ResultRetcodeDescription());
      return false;
   }

    g_msg = "";
    g_dir = direction;
    g_openedThisTick = true;
    MoveChannel(direction, breakLevel);
    ProtectPositions();
    PrintFormat("Nova %s | nivel=%s fill=%s SL=%s TP=%s spread=%d abertas=%d/%d",
                (direction == 1 ? "COMPRA" : "VENDA"),
                DoubleToString(breakLevel, _Digits),
                DoubleToString(trade.ResultPrice(), _Digits),
                DoubleToString(sl, _Digits),
                (tp > 0 ? DoubleToString(tp, _Digits) : "off"),
                sprPts, CountMine(), MaxOrdersCap());
    return true;
}

int CountAgainst(int keepDir)
{
    int n = 0;
    for(int i = PositionsTotal() - 1; i >= 0; i--)
    {
       ulong tk = PositionGetTicket(i);
       if(tk == 0) continue;
       if(!PositionSelectByTicket(tk)) continue;
       if(PositionGetInteger(POSITION_MAGIC) != (long)InpMagicNumber) continue;
       if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
       bool buy = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
       int dir = buy ? 1 : -1;
       if(dir != keepDir) n++;
    }
    return n;
}

int CloseOpposite(int keepDir)
{
    int n = 0;
    for(int i = PositionsTotal() - 1; i >= 0; i--)
    {
       ulong tk = PositionGetTicket(i);
       if(tk == 0) continue;
       if(!PositionSelectByTicket(tk)) continue;
       if(PositionGetInteger(POSITION_MAGIC) != (long)InpMagicNumber) continue;
       if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
       bool buy = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
       int dir = buy ? 1 : -1;
       if(dir == keepDir) continue;
       if(trade.PositionClose(tk))
          n++;
       else
          PrintFormat("Falha ao fechar ticket %I64u na reversao: %s", tk, trade.ResultRetcodeDescription());
    }
    if(n > 0)
       PrintFormat("Reversao: fechadas %d posicoes do lado antigo.", n);
    return n;
}

void TryPendingEntry()
{
    if(g_pendingDir == 0) return;
    if(g_openedThisTick) return;
    if(g_trendNeed > 1 && g_trend != 0 && g_trend != g_pendingDir) return;
    if(CountAgainst(g_pendingDir) > 0)
    {
       CloseOpposite(g_pendingDir);
       return;
    }
    int liveDir = DirMine();
    if(liveDir != 0 && liveDir != g_pendingDir) return;
    if(AtMaxOrders())
    {
       g_pendingDir = 0;
       return;
    }
     if(!AllowEntry(g_pendingDir)) return;
     if(CountMine() > 0 && !LastOrderProtected()) return;
     int dir = g_pendingDir;
    double lvl = g_pendingLevel;
    if(HasOrderOnChannel(dir, lvl))
    {
       g_pendingDir = 0;
       return;
    }
    if(OpenOne(dir, lvl))
       g_pendingDir = 0;
}

void HandleBreak(int direction, double level)
{
    int oldTrend = g_trend;
    RegisterBreak(direction);
    MoveChannel(direction, level);
    SaveCfg();
    TrailStopsOneStep(direction);

    bool reversed = (oldTrend != 0 && g_trend != oldTrend && g_trend == direction);
    if(reversed)
    {
       PrintFormat("Reversao confirmada: %s -> %s. Fecha o cesto e pula este canal.",
                   (oldTrend == 1 ? "ALTA" : "BAIXA"),
                   (direction == 1 ? "ALTA" : "BAIXA"));
       CloseOpposite(direction);
       g_pendingDir = 0;
       g_openedThisTick = true;
       g_msg = "reversao: pulou este canal";
       return;
    }

    int liveDir = DirMine();
    int openN = CountMine();
    if(liveDir != 0) g_dir = liveDir;
    else g_dir = g_trend;

    if(liveDir != 0 && liveDir != direction)
    {
       if(!AllowEntry(direction))
       {
          g_msg = WaitTrendMsg(direction);
          return;
       }
       g_pendingDir = direction;
       g_pendingLevel = level;
       CloseOpposite(direction);
       TryPendingEntry();
       return;
    }

    if(AtMaxOrders())
    {
       g_msg = "maximo de ordens (" + MaxOrdersText() + ")";
       return;
    }

    if(!AllowEntry(direction))
    {
       g_msg = WaitTrendMsg(direction);
       return;
    }

    if(openN > 0 && !LastOrderProtected())
    {
       g_msg = "espera proteger a anterior";
       return;
    }

    g_pendingDir = 0;
    OpenOne(direction, level);
}

void CheckBreakout()
{
   if(!g_botOn || !g_gridReady || g_step <= 0) return;

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if(ask <= 0 || bid <= 0) return;

   int guard = 0;
   while(guard++ < 20)
   {
      bool acted = false;
      if(ask > g_top)
      {
         HandleBreak(1, g_top);
         acted = true;
      }
      else if(bid < g_bottom)
      {
         HandleBreak(-1, g_bottom);
         acted = true;
      }
      if(!acted) break;
      if(g_openedThisTick) break;
      ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   }

   if(StringFind(g_msg, "maximo de ordens") == 0 && !AtMaxOrders())
      g_msg = "";
}

void AddTradeToStats(SStats &st, double net, double pts, bool isTP, bool isSL, bool isProt)
{
   st.trades++;
   st.net    += net;
   st.points += pts;
   if(isTP)        { st.tps++;    st.netTP    += net; }
   else if(isSL)   { st.sls++;    st.netSL    += net; }
   else if(isProt) { st.prots++;  st.netProt  += net; }
   else            { st.others++; st.netOther += net; }

   if(isProt)
   {
      if(net > 0)      st.grossWin  += net;
      else if(net < 0) st.grossLoss += -net;
      return;
   }
   if(net > 0)
   {
      st.wins++;
      st.grossWin += net;
      if(net > st.bestTrade) st.bestTrade = net;
   }
   else if(net < 0)
   {
      st.losses++;
      st.grossLoss += -net;
      if(net < st.worstTrade) st.worstTrade = net;
   }
   else
      st.be++;
}

void RecalcStats()
{
   ZeroMemory(g_all);
   ZeroMemory(g_today);
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   dt.hour = 0; dt.min = 0; dt.sec = 0;
   datetime dayStart = StructToTime(dt);
   if(!HistorySelect(0, TimeCurrent() + 86400)) return;

   int total = HistoryDealsTotal();
   ulong outTickets[];
   ulong posIds[];
   for(int i = 0; i < total; i++)
   {
      ulong t = HistoryDealGetTicket(i);
      if(t == 0) continue;
      if(HistoryDealGetInteger(t, DEAL_MAGIC) != (long)InpMagicNumber) continue;
      if(HistoryDealGetString(t, DEAL_SYMBOL) != _Symbol) continue;
      if(HistoryDealGetInteger(t, DEAL_ENTRY) != DEAL_ENTRY_OUT) continue;
      int n = ArraySize(outTickets);
      ArrayResize(outTickets, n + 1);
      ArrayResize(posIds, n + 1);
      outTickets[n] = t;
      posIds[n]     = (ulong)HistoryDealGetInteger(t, DEAL_POSITION_ID);
   }

   double pt = PointSize();
   int nOut = ArraySize(outTickets);
   for(int k = 0; k < nOut; k++)
   {
      if(!HistorySelectByPosition(posIds[k])) continue;
      double   net = 0, inPrice = 0, outPrice = 0;
      bool     isLong = true, isTP = false, isSL = false, isProt = false;
      datetime closeTime = 0;
      int m = HistoryDealsTotal();
      for(int j = 0; j < m; j++)
      {
         ulong d = HistoryDealGetTicket(j);
         if(d == 0) continue;
         net += HistoryDealGetDouble(d, DEAL_PROFIT) + HistoryDealGetDouble(d, DEAL_SWAP)
              + HistoryDealGetDouble(d, DEAL_COMMISSION) + HistoryDealGetDouble(d, DEAL_FEE);
         if(HistoryDealGetInteger(d, DEAL_ENTRY) == DEAL_ENTRY_IN)
         {
            inPrice = HistoryDealGetDouble(d, DEAL_PRICE);
            isLong  = (HistoryDealGetInteger(d, DEAL_TYPE) == DEAL_TYPE_BUY);
         }
         if(d == outTickets[k])
         {
            outPrice  = HistoryDealGetDouble(d, DEAL_PRICE);
            closeTime = (datetime)HistoryDealGetInteger(d, DEAL_TIME);
            long reason = HistoryDealGetInteger(d, DEAL_REASON);
            isTP = (reason == DEAL_REASON_TP);
            isSL = (reason == DEAL_REASON_SL);
         }
      }
      double pts = 0;
      if(pt > 0 && inPrice > 0 && outPrice > 0)
         pts = (isLong ? (outPrice - inPrice) : (inPrice - outPrice)) / pt;
      if(isSL && pts > 0) { isSL = false; isProt = true; }
      if(g_statsFrom > 0 && closeTime <= g_statsFrom) continue;
      AddTradeToStats(g_all, net, pts, isTP, isSL, isProt);
      if(closeTime >= dayStart)
         AddTradeToStats(g_today, net, pts, isTP, isSL, isProt);
   }
}

//====================================================================
string FmtMoney(double v) { return (v >= 0 ? "+" : "") + DoubleToString(v, 2); }
string FmtPts(double v)   { return (v >= 0 ? "+" : "") + DoubleToString(v, 0) + " pts"; }
color  ColSign(double v)  { return (v > 0) ? clrLimeGreen : (v < 0 ? clrTomato : clrWhite); }

void AddLine(string &L[], color &C[], string text, color clr)
{
   int n = ArraySize(L);
   ArrayResize(L, n + 1);
   ArrayResize(C, n + 1);
   L[n] = text;
   C[n] = clr;
}

void AddStatsBlock(string title, SStats &st, string cur, string &L[], color &C[])
{
   AddLine(L, C, title, clrGold);
   AddLine(L, C, "Ops " + IntegerToString(st.trades) +
                 "  G " + IntegerToString(st.wins) +
                 "  P " + IntegerToString(st.losses) +
                 (st.be > 0 ? "  BE " + IntegerToString(st.be) : ""), clrWhite);
   AddLine(L, C, "Alvo " + IntegerToString(st.tps) + "  " + cur + " " + FmtMoney(st.netTP), ColSign(st.netTP));
   AddLine(L, C, "Stop " + IntegerToString(st.sls) + "  " + cur + " " + FmtMoney(st.netSL), ColSign(st.netSL));
   AddLine(L, C, "Protegida " + IntegerToString(st.prots) + "  " + cur + " " + FmtMoney(st.netProt), ColSign(st.netProt));
   if(st.others > 0)
      AddLine(L, C, "Outras " + IntegerToString(st.others) + "  " + cur + " " + FmtMoney(st.netOther), ColSign(st.netOther));
   AddLine(L, C, "Saldo " + cur + " " + FmtMoney(st.net) + "  (" + FmtPts(st.points) + ")", ColSign(st.net));
}

void PanelLabel(string name, string text, int x, int y, color clr)
{
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_ANCHOR, ANCHOR_LEFT_UPPER);
      ObjectSetString(0, name, OBJPROP_FONT, "Consolas");
      ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 9);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   }
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
}

void DrawButton(string name, int x, int y, string text, color bg, int w = 310, int h = 24)
{
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_BUTTON, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetString(0, name, OBJPROP_FONT, "Consolas");
      ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 9);
      ObjectSetInteger(0, name, OBJPROP_COLOR, clrWhite);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   }
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_XSIZE, w);
   ObjectSetInteger(0, name, OBJPROP_YSIZE, h);
   ObjectSetInteger(0, name, OBJPROP_BGCOLOR, bg);
   ObjectSetInteger(0, name, OBJPROP_BORDER_COLOR, clrDimGray);
   ObjectSetInteger(0, name, OBJPROP_STATE, false);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
}

void ResetScoreNow()
{
   g_statsFrom = TimeCurrent();
   GlobalVariableSet(StatsKey(), (double)g_statsFrom);
   g_resetArm = 0;
   PrintFormat("Placar zerado. Conta ops fechadas depois de %s.",
               TimeToString(g_statsFrom, TIME_DATE | TIME_SECONDS));
   RecalcStats();
}

void ClickResetScore()
{
   if(g_resetArm > 0 && TimeCurrent() - g_resetArm <= 4)
      ResetScoreNow();
   else
   {
      g_resetArm = TimeCurrent();
      g_closeArm = 0;
      Print("Clique de novo em 4s para zerar o placar.");
   }
   UpdatePanel();
}

void CloseAllMine()
{
   int n = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != (long)InpMagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(trade.PositionClose(tk))
         n++;
      else
         PrintFormat("Falha ao fechar ticket %I64u: %s", tk, trade.ResultRetcodeDescription());
   }
   g_closeArm = 0;
   PrintFormat("Fechadas %d posicoes deste bot.", n);
   RecalcStats();
   DrawChannel();
   UpdatePanel();
}

void ClickCloseAll()
{
   if(CountMine() == 0)
   {
      g_msg = "nenhuma posicao aberta";
      g_closeArm = 0;
      UpdatePanel();
      return;
   }
   if(g_closeArm > 0 && TimeCurrent() - g_closeArm <= 4)
      CloseAllMine();
   else
   {
      g_closeArm = TimeCurrent();
      g_resetArm = 0;
      Print("Clique de novo em 4s para FECHAR TODAS as posicoes.");
      UpdatePanel();
   }
}

void DrawEdit(string ed, string lbl, string label, int x, int y, string val)
{
   PanelLabel(lbl, label, x, y + 4, clrWhite);
   if(ObjectFind(0, ed) < 0)
   {
      ObjectCreate(0, ed, OBJ_EDIT, 0, 0, 0);
      ObjectSetInteger(0, ed, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetString(0, ed, OBJPROP_FONT, "Consolas");
      ObjectSetInteger(0, ed, OBJPROP_FONTSIZE, 9);
      ObjectSetInteger(0, ed, OBJPROP_ALIGN, ALIGN_CENTER);
      ObjectSetInteger(0, ed, OBJPROP_READONLY, false);
      ObjectSetInteger(0, ed, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, ed, OBJPROP_BGCOLOR, C'40,44,52');
      ObjectSetInteger(0, ed, OBJPROP_COLOR, clrWhite);
      ObjectSetInteger(0, ed, OBJPROP_BORDER_COLOR, clrDimGray);
      ObjectSetInteger(0, ed, OBJPROP_XSIZE, 90);
      ObjectSetInteger(0, ed, OBJPROP_YSIZE, 22);
      ObjectSetString(0, ed, OBJPROP_TEXT, val);
   }
   ObjectSetInteger(0, ed, OBJPROP_XDISTANCE, x + 160);
   ObjectSetInteger(0, ed, OBJPROP_YDISTANCE, y);
}

void DrawCfgEdits(int x, int y)
{
   DrawEdit(EdCanal(), PNL_PREFIX + "LBLCAN", "Canal (pts):", x, y, IntegerToString(g_channelPts));
   DrawEdit(EdLot(),   PNL_PREFIX + "LBLLOT", "Lote:",        x, y + 26, DoubleToString(g_lot, 2));
   DrawEdit(EdRisk(),  PNL_PREFIX + "LBLRSK", "Risco %:",     x, y + 52, DoubleToString(g_riskPct, 2));
   DrawEdit(EdMax(),   PNL_PREFIX + "LBLMAX", "Max (0=margem):",  x, y + 78, IntegerToString(g_maxOrders));
     DrawEdit(EdRR(),    PNL_PREFIX + "LBLRR",  "RR (2.1=2:1):", x, y + 104, FmtRR());
     DrawEdit(EdLock(),  PNL_PREFIX + "LBLLCK", "Trava (pts):", x, y + 130, IntegerToString(g_lockPts));
     DrawEdit(EdTrend(), PNL_PREFIX + "LBLTRN", "Confirma N:",  x, y + 156, IntegerToString(g_trendNeed));
     DrawEdit(EdDD(),    PNL_PREFIX + "LBLDD",  "Teto DD %:",   x, y + 182, DoubleToString(g_basketDDPct, 2));
     DrawEdit(EdSpr(),   PNL_PREFIX + "LBLSPR", "Spread max:",  x, y + 208, IntegerToString(g_maxSpreadPts));
     DrawEdit(EdScale(), PNL_PREFIX + "LBLSCX", "Lote sobe Nx:", x, y + 234, IntegerToString(g_scaleX));
}

void UpdatePanel()
{
   if(!InpShowPanel) return;
   string cur = AccountInfoString(ACCOUNT_CURRENCY);
   double pt  = PointSize();
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   int n = CountMine();
   int d = DirMine();
   if(d != 0) g_dir = d;

   string L[]; color C[];
   ArrayResize(L, 0); ArrayResize(C, 0);

     AddLine(L, C, "ONECANAL10  |  " + _Symbol + "  |  $10", clrDeepSkyBlue);
    AddLine(L, C, "Linhas: AZUL topo  LARANJA fundo  CINZA ponto0  DOURADO alvo  VERDE trava", clrSilver);
    AddLine(L, C, "Miolo " + IntegerToString(g_channelPts) + " + spread = passo " + IntegerToString(StepPtsLocked()) +
                  " pts | Lote " + DoubleToString(g_lot, 2) +
                  (g_riskPct > 0 ? " | risco " + DoubleToString(g_riskPct, 2) + "%" : ""), clrWhite);
     AddLine(L, C, "Trail 1 passo | Fecha na reversao | Max " + MaxOrdersText() +
                   " (" + IntegerToString(AffordableOrders()) + " cabem) | RR " +
                   (g_rr > 0 ? DoubleToString(g_rr, 2) + ":1" : "off"), clrWhite);
     AddLine(L, C, "Fase lote " + DoubleToString(g_lot, 2) + " desde " +
                   DoubleToString(g_phaseStart, 2) + " | Nx " + IntegerToString(g_scaleX) +
                   " sobe em " + DoubleToString(ScaleTarget(), 2), clrGold);
     AddLine(L, C, "Stop 50% no pico " + DoubleToString(g_peakEq, 2) +
                   " | corta em " + DoubleToString(g_peakEq * 0.50, 2) +
                   (g_killed ? " | KILL" : ""), g_killed ? clrTomato : clrSilver);
     AddLine(L, C, "DD cesto " + (g_basketDDPct > 0 ? DoubleToString(g_basketDDPct, 2) + "%" : "off") +
                   " | Spread max " + (MaxSpreadAllowed() > 0 ? IntegerToString(MaxSpreadAllowed()) + " pts" : "off") +
                   " (agora " + IntegerToString(SpreadPtsNow()) + ")", clrWhite);
     AddLine(L, C, TrendText(), (g_trend == 1 ? clrLimeGreen : (g_trend == -1 ? clrTomato : clrSilver)));
   AddLine(L, C, "----------------------------------------", clrDimGray);

   if(g_gridReady)
   {
       AddLine(L, C, "Ponto 0: " + DoubleToString(g_origin, _Digits) + " | Canal " +
                     DoubleToString(g_bottom, _Digits) + " / " + DoubleToString(g_top, _Digits), clrWhite);
       AddLine(L, C, "Falta compra: " + DoubleToString(MathMax(0, (g_top - ask) / pt), 0) + " pts", clrWhite);
       AddLine(L, C, "Falta venda: " + DoubleToString(MathMax(0, (bid - g_bottom) / pt), 0) + " pts", clrWhite);
   }
   else
      AddLine(L, C, "Canal: aguardando...", clrSilver);

    if(n > 0)
       AddLine(L, C, (g_dir == 1 ? "STATUS: COMPRANDO " : "STATUS: VENDENDO ") + IntegerToString(n) + "/" + MaxOrdersText(),
               g_dir == 1 ? clrLimeGreen : clrTomato);
    else
       AddLine(L, C, g_botOn ? "STATUS: aguardando rompimento" : "STATUS: BOT DESLIGADO",
               g_botOn ? clrSilver : clrTomato);

    AddLine(L, C, (g_msg == "") ? " " : g_msg, clrOrange);
    AddLine(L, C, "----------------------------------------", clrDimGray);
    AddStatsBlock("HOJE", g_today, cur, L, C);
    AddLine(L, C, "----------------------------------------", clrDimGray);
    AddStatsBlock(g_statsFrom > 0 ? "TOTAL (desde " + TimeToString(g_statsFrom, TIME_DATE | TIME_MINUTES) + ")" : "TOTAL", g_all, cur, L, C);

   int lineH = 16, padX = 10, padY = 8;
   int lines = ArraySize(L);
   g_panelLines = lines;
     g_panelH = lines * lineH + 2 * padY + 348;
     if(g_panelH < 446) g_panelH = 446;
     for(int k = lines; k < lines + 20; k++)
       ObjectDelete(0, PNL_PREFIX + "L" + IntegerToString(k));

   int cw = (int)ChartGetInteger(0, CHART_WIDTH_IN_PIXELS);
   int chh = (int)ChartGetInteger(0, CHART_HEIGHT_IN_PIXELS);
   if(cw > 0)  g_panelX = MathMax(0, MathMin(g_panelX, cw - PANEL_W));
   if(chh > 0) g_panelY = MathMax(0, MathMin(g_panelY, chh - g_panelH));
   int x0 = g_panelX, y0 = g_panelY;

   string bg = PNL_PREFIX + "BG";
   if(ObjectFind(0, bg) < 0)
   {
      ObjectCreate(0, bg, OBJ_RECTANGLE_LABEL, 0, 0, 0);
      ObjectSetInteger(0, bg, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, bg, OBJPROP_BGCOLOR, C'20,24,32');
      ObjectSetInteger(0, bg, OBJPROP_BORDER_TYPE, BORDER_FLAT);
      ObjectSetInteger(0, bg, OBJPROP_COLOR, clrDimGray);
      ObjectSetInteger(0, bg, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, bg, OBJPROP_HIDDEN, true);
   }
   ObjectSetInteger(0, bg, OBJPROP_XDISTANCE, x0);
   ObjectSetInteger(0, bg, OBJPROP_YDISTANCE, y0);
   ObjectSetInteger(0, bg, OBJPROP_XSIZE, PANEL_W);
   ObjectSetInteger(0, bg, OBJPROP_YSIZE, g_panelH);

   for(int i = 0; i < lines; i++)
      PanelLabel(PNL_PREFIX + "L" + IntegerToString(i), L[i], x0 + padX, y0 + padY + i * lineH, C[i]);

   int by = y0 + padY + lines * lineH + 4;
    DrawButton(BtnName(), x0 + padX, by,
               g_botOn ? "BOT LIGADO - clique p/ DESLIGAR" : "BOT DESLIGADO - clique p/ LIGAR",
               g_botOn ? C'0,110,60' : C'150,40,40');
    bool rstArm = (g_resetArm > 0 && TimeCurrent() - g_resetArm <= 4);
    bool cloArm = (g_closeArm > 0 && TimeCurrent() - g_closeArm <= 4);
    DrawButton(BtnResetName(), x0 + padX, by + 28,
               rstArm ? "CONFIRMA ZERAR PLACAR" : "ZERAR PLACAR",
               rstArm ? C'180,120,0' : C'70,70,90', 200, 22);
    DrawButton(BtnCloseName(), x0 + padX + 206, by + 28,
               cloArm ? "CONFIRMA" : "X ALL",
               cloArm ? C'180,40,40' : C'120,30,30', 104, 22);
    DrawCfgEdits(x0 + padX, by + 54);
    ChartRedraw(0);
}

void MovePanel()
{
   int lineH = 16, padX = 10, padY = 8;
   ObjectSetInteger(0, PNL_PREFIX + "BG", OBJPROP_XDISTANCE, g_panelX);
   ObjectSetInteger(0, PNL_PREFIX + "BG", OBJPROP_YDISTANCE, g_panelY);
   for(int i = 0; i < g_panelLines; i++)
   {
      string nm = PNL_PREFIX + "L" + IntegerToString(i);
      ObjectSetInteger(0, nm, OBJPROP_XDISTANCE, g_panelX + padX);
      ObjectSetInteger(0, nm, OBJPROP_YDISTANCE, g_panelY + padY + i * lineH);
   }
   int by = g_panelY + padY + g_panelLines * lineH + 4;
    ObjectSetInteger(0, BtnName(), OBJPROP_XDISTANCE, g_panelX + padX);
    ObjectSetInteger(0, BtnName(), OBJPROP_YDISTANCE, by);
    ObjectSetInteger(0, BtnResetName(), OBJPROP_XDISTANCE, g_panelX + padX);
    ObjectSetInteger(0, BtnResetName(), OBJPROP_YDISTANCE, by + 28);
    ObjectSetInteger(0, BtnCloseName(), OBJPROP_XDISTANCE, g_panelX + padX + 206);
    ObjectSetInteger(0, BtnCloseName(), OBJPROP_YDISTANCE, by + 28);
     string eds[10]  = {EdCanal(), EdLot(), EdRisk(), EdMax(), EdRR(), EdLock(), EdTrend(), EdDD(), EdSpr(), EdScale()};
     string lbls[10] = {PNL_PREFIX + "LBLCAN", PNL_PREFIX + "LBLLOT", PNL_PREFIX + "LBLRSK", PNL_PREFIX + "LBLMAX", PNL_PREFIX + "LBLRR", PNL_PREFIX + "LBLLCK", PNL_PREFIX + "LBLTRN", PNL_PREFIX + "LBLDD", PNL_PREFIX + "LBLSPR", PNL_PREFIX + "LBLSCX"};
     for(int i = 0; i < 10; i++)
    {
       ObjectSetInteger(0, eds[i], OBJPROP_XDISTANCE, g_panelX + padX + 160);
       ObjectSetInteger(0, eds[i], OBJPROP_YDISTANCE, by + 54 + i * 26);
       ObjectSetInteger(0, lbls[i], OBJPROP_XDISTANCE, g_panelX + padX);
       ObjectSetInteger(0, lbls[i], OBJPROP_YDISTANCE, by + 58 + i * 26);
    }
   ChartRedraw(0);
}

void ToggleBot()
{
   g_botOn = !g_botOn;
   GlobalVariableSet(GVKey(), g_botOn ? 1 : 0);
   if(!g_botOn)
   {
      Print("OneCanal10 DESLIGADO. Sem novas entradas. Ordens abertas seguem com stop.");
      g_msg = "bot desligado";
   }
    else
    {
       if(g_killed)
       {
          double eq = EquityNow();
          if(g_peakEq > 0 && eq <= g_peakEq * 0.50)
          {
             g_botOn = false;
             GlobalVariableSet(GVKey(), 0);
             g_msg = "STOP 50% ativo. Nao religa abaixo da metade do pico.";
             Print(g_msg);
             UpdatePanel();
             return;
          }
          g_killed = false;
       }
        Print("OneCanal10 LIGADO.");
        g_msg = "";
        if(!g_gridReady)
           MarkChannelAtPrice();
    }
   UpdatePanel();
}

void ApplyCfgEdit(const string sparam)
{
   if(sparam == EdCanal())
   {
      int val = (int)StringToInteger(ObjectGetString(0, EdCanal(), OBJPROP_TEXT));
      if(val < 1)
         PrintFormat("Canal minimo 1 pt. Mantido %d.", g_channelPts);
      else
      {
           g_channelPts = val;
           if(CountMine() == 0)
           {
              g_gridReady = false;
              g_origin = 0;
              g_step = ComputeStep();
              if(g_botOn) MarkChannelAtPrice();
           }
          SaveCfg();
          PrintFormat("Canal agora: %d pts", g_channelPts);
      }
      ObjectSetString(0, EdCanal(), OBJPROP_TEXT, IntegerToString(g_channelPts));
   }
   else if(sparam == EdLot())
   {
      double val = StringToDouble(ObjectGetString(0, EdLot(), OBJPROP_TEXT));
      if(val <= 0)
         PrintFormat("Lote invalido. Mantido %.2f.", g_lot);
      else
      {
          g_lot = val;
          g_phaseStart = AccountInfoDouble(ACCOUNT_BALANCE);
          SaveCfg();
          PrintFormat("Lote agora: %.2f | fase reset $%.2f", g_lot, g_phaseStart);
      }
      ObjectSetString(0, EdLot(), OBJPROP_TEXT, DoubleToString(g_lot, 2));
   }
   else if(sparam == EdRisk())
   {
      double val = StringToDouble(ObjectGetString(0, EdRisk(), OBJPROP_TEXT));
      if(val < 0 || val > 100)
         PrintFormat("Risco invalido. Mantido %.2f%%.", g_riskPct);
      else
      {
         g_riskPct = val;
         SaveCfg();
         PrintFormat("Risco agora: %.2f%% (0 = lote fixo)", g_riskPct);
      }
      ObjectSetString(0, EdRisk(), OBJPROP_TEXT, DoubleToString(g_riskPct, 2));
   }
   else if(sparam == EdMax())
   {
      int val = (int)StringToInteger(ObjectGetString(0, EdMax(), OBJPROP_TEXT));
      if(val < 0 || val > 1000)
         PrintFormat("Max ordens invalido. Mantido %d.", g_maxOrders);
      else
      {
         g_maxOrders = val;
         SaveCfg();
         PrintFormat("Maximo de ordens: %s", MaxOrdersText());
      }
      ObjectSetString(0, EdMax(), OBJPROP_TEXT, IntegerToString(g_maxOrders));
   }
    else if(sparam == EdRR())
    {
       bool ok = false;
       double val = ParseRRText(ObjectGetString(0, EdRR(), OBJPROP_TEXT), ok);
       if(!ok)
          PrintFormat("RR invalido. Use 0, 1.1, 2.1, 3.1 ou 2:1. Mantido %s.", FmtRR());
       else
       {
          g_rr = val;
          SaveCfg();
          PrintFormat("RR agora: %s", (g_rr > 0 ? DoubleToString(g_rr, 2) + ":1" : "off"));
          if(g_rr > 0) ProtectPositions();
       }
       ObjectSetString(0, EdRR(), OBJPROP_TEXT, FmtRR());
    }
    else if(sparam == EdLock())
    {
       int val = (int)StringToInteger(ObjectGetString(0, EdLock(), OBJPROP_TEXT));
       if(val < 0 || val > 100000)
          PrintFormat("Trava invalida. Mantida %d pts.", g_lockPts);
       else
       {
          g_lockPts = val;
          SaveCfg();
          PrintFormat("Trava agora: %s", (g_lockPts > 0 ? IntegerToString(g_lockPts) + " pts" : "off"));
          if(g_lockPts > 0) ProtectPositions();
       }
       ObjectSetString(0, EdLock(), OBJPROP_TEXT, IntegerToString(g_lockPts));
    }
    else if(sparam == EdTrend())
    {
       int val = (int)StringToInteger(ObjectGetString(0, EdTrend(), OBJPROP_TEXT));
       if(val < 1 || val > 50)
          PrintFormat("Confirma invalido. Mantido %d.", g_trendNeed);
       else
       {
          g_trendNeed = val;
          if(g_streak >= g_trendNeed && g_streakDir != 0)
             g_trend = g_streakDir;
          SaveCfg();
          PrintFormat("Confirma tendencia: %s", (g_trendNeed <= 1 ? "off" : IntegerToString(g_trendNeed) + " canais"));
       }
        ObjectSetString(0, EdTrend(), OBJPROP_TEXT, IntegerToString(g_trendNeed));
    }
    else if(sparam == EdDD())
    {
       double val = StringToDouble(ObjectGetString(0, EdDD(), OBJPROP_TEXT));
       if(val < 0 || val > 100)
          PrintFormat("Teto DD invalido. Mantido %.2f%%.", g_basketDDPct);
       else
       {
          g_basketDDPct = val;
          SaveCfg();
          PrintFormat("Teto DD do cesto: %s", (g_basketDDPct > 0 ? DoubleToString(g_basketDDPct, 2) + "%" : "off"));
       }
       ObjectSetString(0, EdDD(), OBJPROP_TEXT, DoubleToString(g_basketDDPct, 2));
    }
    else if(sparam == EdSpr())
    {
       int val = (int)StringToInteger(ObjectGetString(0, EdSpr(), OBJPROP_TEXT));
       if(val < 0 || val > 100000)
          PrintFormat("Spread max invalido. Mantido %d.", g_maxSpreadPts);
       else
       {
          g_maxSpreadPts = val;
          SaveCfg();
          PrintFormat("Spread max: %s", (g_maxSpreadPts > 0 ? IntegerToString(g_maxSpreadPts) + " pts" : "auto (metade do canal)"));
       }
       ObjectSetString(0, EdSpr(), OBJPROP_TEXT, IntegerToString(g_maxSpreadPts));
    }
    else if(sparam == EdScale())
    {
       int val = (int)StringToInteger(ObjectGetString(0, EdScale(), OBJPROP_TEXT));
       if(g_botOn)
          PrintFormat("Lote Nx so muda com o bot DESLIGADO. Mantido %d.", g_scaleX);
       else if(val < 1 || val > 100)
          PrintFormat("Lote Nx invalido (1-100). Mantido %d.", g_scaleX);
       else
       {
          g_scaleX = val;
          SaveCfg();
          PrintFormat("Lote sobe a cada %dx de lucro da fase.", g_scaleX);
       }
       ObjectSetString(0, EdScale(), OBJPROP_TEXT, IntegerToString(g_scaleX));
    }
    UpdatePanel();
}

//====================================================================
int OnInit()
{
   if(InpChannelPts < 1)
   {
      Print("Canal minimo 1 pt.");
      return(INIT_PARAMETERS_INCORRECT);
   }

    trade.SetExpertMagicNumber(InpMagicNumber);
    trade.SetDeviationInPoints(InpSlippagePts);
    SetupFilling();
    LoadCfg();

     if(!MQLInfoInteger(MQL_TESTER) && GlobalVariableCheck(GVKey()))
        g_botOn = (GlobalVariableGet(GVKey()) != 0);
    if(GlobalVariableCheck(StatsKey()))
       g_statsFrom = (datetime)(long)GlobalVariableGet(StatsKey());

    int live = CountMine();
    if(live > 0)
       g_dir = DirMine();
    else if(g_trend != 0)
       g_dir = g_trend;

    if(g_gridReady && g_origin > 0 && g_step > 0)
       DrawChannel();
    else if(g_botOn)
       MarkChannelAtPrice();

    RecalcStats();
    if(InpShowPanel)
    {
       if(GlobalVariableCheck(PosKey("X"))) g_panelX = (int)GlobalVariableGet(PosKey("X"));
       if(GlobalVariableCheck(PosKey("Y"))) g_panelY = (int)GlobalVariableGet(PosKey("Y"));
       ChartSetInteger(0, CHART_EVENT_MOUSE_MOVE, true);
       EventSetTimer(5);
       UpdatePanel();
    }

     EnsurePhaseStart();
     if(g_killed) g_botOn = false;
     PrintFormat("OneCanal10 iniciado | %s | miolo=%d passo=%d | max=%s | lote=%.2f | Nx=%d | fase=$%.2f | pico=$%.2f | confirma=%d | DD=%.2f%% | spreadmax=%d",
                _Symbol, g_channelPts, StepPtsLocked(), MaxOrdersText(), g_lot, g_scaleX, g_phaseStart, g_peakEq, g_trendNeed, g_basketDDPct, g_maxSpreadPts);
    if(InpLogCsv)
       PrintFormat("CSV: Terminal -> Arquivo -> Abrir pasta comum de dados -> Files\\%s", CsvFile());
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   FlushCsvFromHistory();
   EventKillTimer();
   if(g_dragging) ChartSetInteger(0, CHART_MOUSE_SCROLL, g_scrollWas);
   ChartSetInteger(0, CHART_EVENT_MOUSE_MOVE, false);
    ObjectsDeleteAll(0, PNL_PREFIX);
    ClearChartLines();
}

void OnTimer()
{
   UpdatePanel();
}

void CloseBasketDD()
{
   if(!BasketDDHit()) return;
   int n = CountMine();
   if(n <= 0) return;
   PrintFormat("Teto DD do cesto: fecha %d posicoes (pnl=%.2f, limite=%.2f%%).",
               n, OpenPnl(), g_basketDDPct);
   CloseAllMine();
   g_pendingDir = 0;
   g_openedThisTick = true;
   g_retryAt = TimeCurrent() + 2;
   g_msg = "teto DD do cesto: cesto fechado";
}

void OnTick()
{
    g_openedThisTick = false;
    CheckAccountStop();
    ScaleLotByProfit();
    CloseBasketDD();
    if(g_trendNeed > 1 && g_trend != 0 && CountAgainst(g_trend) > 0)
    {
       CloseOpposite(g_trend);
       if(g_pendingDir == 0)
       {
          g_pendingDir = g_trend;
          g_pendingLevel = (g_trend == 1) ? g_top : g_bottom;
       }
    }
    TryPendingEntry();
    if(CountMine() > 0)
       ProtectPositions();
    if(!g_botOn) return;
    if(!g_gridReady) MarkChannelAtPrice();
    CheckBreakout();
}

void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   if(trans.type != TRADE_TRANSACTION_DEAL_ADD) return;
   if(!HistoryDealSelect(trans.deal)) return;
   if(HistoryDealGetInteger(trans.deal, DEAL_MAGIC) != (long)InpMagicNumber) return;
   if(HistoryDealGetString(trans.deal, DEAL_SYMBOL) != _Symbol) return;
   if(HistoryDealGetInteger(trans.deal, DEAL_ENTRY) != DEAL_ENTRY_OUT) return;

    LogTradeClose(trans.deal);
    Print("Ordem fechada. As outras seguem.");
    RecalcStats();
    if(CountMine() == 0)
       g_dir = g_trend;
    DrawChannel();
    UpdatePanel();
}

void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
{
    if(id == CHARTEVENT_OBJECT_CLICK && sparam == BtnName())
    {
       ToggleBot();
       return;
    }
    if(id == CHARTEVENT_OBJECT_CLICK && sparam == BtnResetName())
    {
       ClickResetScore();
       return;
    }
    if(id == CHARTEVENT_OBJECT_CLICK && sparam == BtnCloseName())
    {
       ClickCloseAll();
       return;
    }
     if(id == CHARTEVENT_OBJECT_ENDEDIT &&
         (sparam == EdCanal() || sparam == EdLot() || sparam == EdRisk() || sparam == EdMax() || sparam == EdRR() || sparam == EdLock() || sparam == EdTrend() || sparam == EdDD() || sparam == EdSpr() || sparam == EdScale()))
    {
       ApplyCfgEdit(sparam);
       return;
    }
   if(id == CHARTEVENT_MOUSE_MOVE && InpShowPanel)
   {
      int  mx = (int)lparam;
      int  my = (int)dparam;
      bool down = ((uint)StringToInteger(sparam) & 1) != 0;
      if(down && !g_prevDown && !g_dragging)
      {
         if(mx >= g_panelX && mx <= g_panelX + PANEL_W &&
            my >= g_panelY && my <= g_panelY + PANEL_TITLE_H)
         {
            g_dragging = true;
            g_dragDX = mx - g_panelX;
            g_dragDY = my - g_panelY;
            g_scrollWas = (bool)ChartGetInteger(0, CHART_MOUSE_SCROLL);
            ChartSetInteger(0, CHART_MOUSE_SCROLL, false);
         }
      }
      else if(down && g_dragging)
      {
         int cw = (int)ChartGetInteger(0, CHART_WIDTH_IN_PIXELS);
         int chh = (int)ChartGetInteger(0, CHART_HEIGHT_IN_PIXELS);
         int nx = MathMax(0, MathMin(mx - g_dragDX, cw - PANEL_W));
         int ny = MathMax(0, MathMin(my - g_dragDY, chh - g_panelH));
         if(nx != g_panelX || ny != g_panelY)
         {
            g_panelX = nx;
            g_panelY = ny;
            MovePanel();
         }
      }
      else if(!down && g_dragging)
      {
         g_dragging = false;
         ChartSetInteger(0, CHART_MOUSE_SCROLL, g_scrollWas);
         GlobalVariableSet(PosKey("X"), g_panelX);
         GlobalVariableSet(PosKey("Y"), g_panelY);
      }
      g_prevDown = down;
   }
}
//+------------------------------------------------------------------+
