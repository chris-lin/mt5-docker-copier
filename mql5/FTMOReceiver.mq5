//+------------------------------------------------------------------+
//|                                               FTMOReceiver.mq5   |
//+------------------------------------------------------------------+
#property strict
#include <Trade\Trade.mqh>

#import "ws2_32.dll"
   int WSAStartup(ushort wVersionRequested, uchar &lpWSAData[]);
   int WSACleanup();
   ulong socket(int af, int type, int protocol);
   int connect(ulong s, uchar &name[], int namelen);
   int recv(ulong s, uchar &buf[], int len, int flags);
   int closesocket(ulong s);
   int ioctlsocket(ulong s, long cmd, uint &argp);
   uint inet_addr(uchar &cp[]);
   ushort htons(ushort hostshort);
#import

#define AF_INET        2
#define SOCK_STREAM    1
#define IPPROTO_TCP    6
#define FIONBIO        0x8004667E // 設定 Non-blocking 模式的指令常數
#define INVALID_SOCKET (ulong)(~0)

CTrade trade;
input string HOST = "172.18.0.1";
input int    PORT = 9002;

ulong client_sock = INVALID_SOCKET;
datetime last_reconnect = 0;
string rx_buffer = "";

void PrepareSockAddr(uchar &addr[], string ip, ushort port)
{
   ArrayResize(addr, 16);
   ArrayInitialize(addr, 0);
   
   addr[0] = (uchar)(AF_INET & 0xFF);
   addr[1] = (uchar)((AF_INET >> 8) & 0xFF);
   
   ushort net_port = htons(port);
   addr[2] = (uchar)(net_port & 0xFF);
   addr[3] = (uchar)((net_port >> 8) & 0xFF);
   
   uchar ip_chars[];
   StringToCharArray(ip, ip_chars, 0, WHOLE_ARRAY, CP_ACP);
   uint net_ip = inet_addr(ip_chars);
   
   addr[4] = (uchar)(net_ip & 0xFF);
   addr[5] = (uchar)((net_ip >> 8) & 0xFF);
   addr[6] = (uchar)((net_ip >> 16) & 0xFF);
   addr[7] = (uchar)((net_ip >> 24) & 0xFF);
}

bool ConnectToHub()
{
   if(client_sock != INVALID_SOCKET)
   {
      closesocket(client_sock);
      client_sock = INVALID_SOCKET;
   }
   
   client_sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
   if(client_sock == INVALID_SOCKET) return false;
   
   uchar addr[];
   PrepareSockAddr(addr, HOST, (ushort)PORT);
   
   if(connect(client_sock, addr, 16) != 0)
   {
      closesocket(client_sock);
      client_sock = INVALID_SOCKET;
      return false;
   }
   
   // 切換為非阻塞模式 (argp = 1)
   uint non_block = 1;
   ioctlsocket(client_sock, FIONBIO, non_block);
   
   Print("[Winsock] FTMO Receiver connected to Hub successfully!");
   return true;
}

void ExecuteCommand(string cmd)
{
   Print("[FTMO] Executing: ", cmd);
   string parts[];
   int count = StringSplit(cmd, ';', parts);
   if(count < 2) return;
   
   if(parts[0] == "OPEN" && count >= 7)
   {
      string symbol = parts[1];
      ENUM_ORDER_TYPE orderType = (parts[2] == "0") ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
      double volume = StringToDouble(parts[3]);
      double sl = StringToDouble(parts[4]);
      double tp = StringToDouble(parts[5]);
      string comment = "Src:" + parts[6];
      
      // === 加入這行：確保該商品存在於 Market Watch 中 ===
      if(!SymbolSelect(symbol, true))
      {
         Print("[FTMO] Failed to select symbol: ", symbol, " Error: ", GetLastError());
         return;
      }
      
      // 確保獲取到最新報價再下單
      MqlTick tick;
      if(!SymbolInfoTick(symbol, tick))
      {
         Print("[FTMO] Waiting for first tick for: ", symbol);
         // 等待極短時間或直接下單由 CTrade 處理
      }
      
      if(orderType == ORDER_TYPE_BUY)
         trade.Buy(volume, symbol, 0, sl, tp, comment);
      else
         trade.Sell(volume, symbol, 0, sl, tp, comment);
   }
   else if(parts[0] == "CLOSE" && count >= 3)
   {
      string target_comment = "Src:" + parts[2];
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         ulong ticket = PositionGetTicket(i);
         if(ticket > 0 && PositionGetString(POSITION_COMMENT) == target_comment)
         {
            trade.PositionClose(ticket);
         }
      }
   }
}

int OnInit()
{
   Print("=== FTMOReceiver DEBUG ===");
   Print("ProgramType = ", MQLInfoInteger(MQL_PROGRAM_TYPE));
   Print("ProgramPath = ", MQLInfoString(MQL_PROGRAM_PATH));
   Print("ProgramName = ", MQLInfoString(MQL_PROGRAM_NAME));

   uchar wsaData[512];
   WSAStartup(0x0202, wsaData);
   
   ConnectToHub();
   EventSetMillisecondTimer(50); // 50ms 檢查一次收到的指令
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   if(client_sock != INVALID_SOCKET)
   {
      closesocket(client_sock);
      client_sock = INVALID_SOCKET;
   }
   WSACleanup();
}

void OnTimer()
{
   if(client_sock == INVALID_SOCKET)
   {
      if(TimeCurrent() - last_reconnect >= 3)
      {
         last_reconnect = TimeCurrent();
         ConnectToHub();
      }
      return;
   }
   
   uchar buf[1024];
   int rec = recv(client_sock, buf, 1024, 0);
   
   if(rec > 0)
   {
      string msg = CharArrayToString(buf, 0, rec, CP_UTF8);
      rx_buffer += msg;
      
      while(StringFind(rx_buffer, "\n") >= 0)
      {
         int pos = StringFind(rx_buffer, "\n");
         string line = StringSubstr(rx_buffer, 0, pos);
         rx_buffer = StringSubstr(rx_buffer, pos + 1);
         StringTrimRight(line);
         if(StringLen(line) > 0)
         {
            ExecuteCommand(line);
         }
      }
   }
   else if(rec == 0) // 對方正常斷線
   {
      Print("[Winsock] Hub disconnected");
      closesocket(client_sock);
      client_sock = INVALID_SOCKET;
   }
   // 若 rec < 0 且在非阻塞模式下代表暫無資料，忽略即可
}