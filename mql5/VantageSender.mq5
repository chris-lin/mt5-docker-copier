//+------------------------------------------------------------------+
//|                                                VantageSender.mq5 |
//+------------------------------------------------------------------+
#property strict

#import "ws2_32.dll"
   int WSAStartup(ushort wVersionRequested, uchar &lpWSAData[]);
   int WSACleanup();
   ulong socket(int af, int type, int protocol);
   int connect(ulong s, uchar &name[], int namelen);
   int send(ulong s, const uchar &buf[], int len, int flags);
   int closesocket(ulong s);
   uint inet_addr(uchar &cp[]);
   ushort htons(ushort hostshort);
#import

#define AF_INET     2
#define SOCK_STREAM 1
#define IPPROTO_TCP 6
#define INVALID_SOCKET (ulong)(~0)

input string HOST = "172.18.0.1";
input int    PORT = 9001;

ulong client_sock = INVALID_SOCKET;
datetime last_reconnect = 0;

// 將 IP 與 Port 打包成 sockaddr_in 結構 (16 bytes)
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
   
   Print("[Winsock] Connected to Hub successfully!");
   return true;
}

int OnInit()
{
   Print("=== VantageSender DEBUG ===");
   Print("ProgramType = ", MQLInfoInteger(MQL_PROGRAM_TYPE));
   Print("ProgramPath = ", MQLInfoString(MQL_PROGRAM_PATH));
   Print("ProgramName = ", MQLInfoString(MQL_PROGRAM_NAME));

   uchar wsaData[512];
   WSAStartup(0x0202, wsaData); // 初始化 Winsock 2.2
   
   ConnectToHub();
   EventSetMillisecondTimer(200);
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
   
   string json = "[";
   int total = PositionsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0)
      {
         string item = StringFormat(
            "{\"ticket\":%I64u,\"symbol\":\"%s\",\"type\":%d,\"volume\":%.2f,\"price\":%.5f,\"sl\":%.5f,\"tp\":%.5f}",
            ticket,
            PositionGetString(POSITION_SYMBOL),
            (int)PositionGetInteger(POSITION_TYPE),
            PositionGetDouble(POSITION_VOLUME),
            PositionGetDouble(POSITION_PRICE_OPEN),
            PositionGetDouble(POSITION_SL),
            PositionGetDouble(POSITION_TP)
         );
         json += item;
         if(i < total - 1) json += ",";
      }
   }
   json += "]\n";
   
   int len = StringLen(json);
   uchar send_buf[];
   StringToCharArray(json, send_buf, 0, len, CP_UTF8);
   
   int ret = send(client_sock, send_buf, len, 0);
   if(ret <= 0)
   {
      Print("[Winsock] Send failed, closing socket");
      closesocket(client_sock);
      client_sock = INVALID_SOCKET;
   }
}