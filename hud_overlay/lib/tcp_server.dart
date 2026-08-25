import 'dart:async';
import 'dart:convert';
import 'dart:io';

/// SCENE 协议 v1：core 持 scene → UI 纯投影。
/// hello（握手）→ welcome（回应 + caps）→ scene 全量 / patch 增量。
class TcpFrame {
  final String type; // 'hello' | 'welcome' | 'scene' | 'patch' | legacy 命令前缀
  final String raw; // 完整原始行
  final dynamic payload; // JSON 解析后的对象；非 JSON 帧为 null

  TcpFrame(this.type, this.raw, this.payload);
}

class JarvisTCPServer {
  final int port;
  ServerSocket? _server;
  Function(String)? onMessage;
  /// SCENE 帧回调（解析后的结构化帧；仅当行是 SCENE 协议帧时触发）
  Function(TcpFrame)? onFrame;
  /// 已连接的 socket 引用（用于 hello 后回 welcome）
  Socket? _currentSocket;

  JarvisTCPServer({this.port = 17889});

  Future<void> start() async {
    try {
      _server = await ServerSocket.bind(InternetAddress.loopbackIPv4, port);
      print('TCP Server listening on port $port');
      _server!.listen(_handleClient);
    } catch (e) {
      print('Failed to start TCP server: $e');
    }
  }

  void _handleClient(Socket socket) {
    print('[TCP] Client connected');
    socket.listen(
      (data) {
        final message = utf8.decode(data, allowMalformed: true).trim();
        if (message.isNotEmpty) {
          final lines = message.split('\n');
          for (var line in lines) {
            if (line.isNotEmpty) {
              print('[TCP] Received raw: $line');
              _dispatch(line, socket);
            }
          }
        }
      },
      onDone: () {
        print('[TCP] Client disconnected');
        socket.close();
      },
      onError: (e) {
        print('[TCP] Socket error: $e');
        socket.close();
      },
    );
  }

  /// 分派：SCENE 协议帧（hello/scene/patch/…:json）走 onFrame，
  /// 其余（legacy 命令如 wake/user:…/ai:…）走 onMessage。
  void _dispatch(String line, Socket socket) {
    _currentSocket = socket;
    final idx = line.indexOf(':');
    if (idx > 0) {
      final prefix = line.substring(0, idx).trim();
      final knownFrames = {
        'hello', 'welcome', 'scene', 'patch',
      };
      if (knownFrames.contains(prefix)) {
        dynamic payload;
        try {
          payload = jsonDecode(line.substring(idx + 1));
        } catch (_) {
          payload = null;
        }
        final frame = TcpFrame(prefix, line, payload);
        if (onFrame != null) {
          onFrame!(frame);
        } else {
          onMessage?.call(line);
        }
        return;
      }
    }
    onMessage?.call(line);
  }

  /// 向当前连接的客户端回发一条消息（握手 welcome 用）。
  bool reply(String message) {
    final sock = _currentSocket;
    if (sock == null) return false;
    try {
      sock.write('$message\n');
      return true;
    } catch (_) {
      return false;
    }
  }

  Future<void> stop() async {
    await _server?.close();
    _server = null;
  }
}
