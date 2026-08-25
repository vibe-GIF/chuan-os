import 'dart:convert';
import 'package:flutter/material.dart';
import 'agent_visual.dart';
import 'jarvis_overlay.dart';
import 'tcp_server.dart';
import 'linmeimei_overlay.dart';

/// Agent 特效调度器 — 根据命令切换不同 Agent 的可视化特效
///
/// 新增 Agent 只需：
///   1. 创建新类实现 AgentVisual
///   2. 在 _initAgents 中注册
///   3. 通过 TCP 发送 `agent:xxx` 切换
///
/// SCENE 协议 v1（N34）：core 持 scene → UI 纯投影。
///   - 收到 `hello:{json}` → 回 `welcome:{json}`（含 caps + 全量 scene 请求）
///   - 收到 `scene:{json}` → 全量投影
///   - 收到 `patch:{json}` → 增量投影（只更新变化字段）
///   同一协议可被手机 PWA 复用（TCP / WebSocket 只换传输层）。
class AgentOverlay extends StatefulWidget {
  final double screenHeight;

  const AgentOverlay({super.key, required this.screenHeight});

  @override
  State<AgentOverlay> createState() => _AgentOverlayState();
}

class _AgentOverlayState extends State<AgentOverlay>
    with TickerProviderStateMixin {
  final JarvisTCPServer _tcpServer = JarvisTCPServer();
  final Map<String, AgentVisual> _agents = {};
  String _currentAgentName = 'jarvis';
  DateTime? _lastWakeTime;
  static const _wakeCooldownMs = 1500;

  /// SCENE 协议：当前持有的 scene 状态（core 全量 + 增量合并后的投影源）
  Map<String, dynamic> _scene = {
    'version': 1,
    'agent': 'jarvis',
    'effect': 'idle',
    'user': {'text': '', 'ts': ''},
    'ai': {'text': '', 'ts': ''},
    'monitor': {},
    'tool_call': '',
  };

  static const _sceneCaps = [
    'scene', 'patch', 'hello', 'welcome', 'monitor',
    'wake', 'hide', 'agent', 'user', 'ai', 'effect', 'tool_call',
  ];

  @override
  void initState() {
    super.initState();
    _initAgents();
    _initTCPServer();
  }

  void _initAgents() {
    print('[AgentOverlay] 预加载所有 Agent 模型...');
    _agents['jarvis'] = JarvisAgentVisual(vsync: this);

    _agents['lin-meimei'] = LinMeimeiPet(
      vsync: this,
      onModelReady: () {
        setState(() {});
      },
    );

    print('[AgentOverlay] 所有 Agent 初始化完成');
  }

  void _initTCPServer() {
    _tcpServer.onMessage = _handleCommand;
    _tcpServer.onFrame = _handleSceneFrame;
    _tcpServer.start();
  }

  // ------------------------------------------------------------------ #
  // SCENE 协议帧处理（hello 握手 + scene 全量 + patch 增量投影）
  // ------------------------------------------------------------------ #
  void _handleSceneFrame(TcpFrame frame) {
    print('[SCENE] frame=${frame.type} payload=${frame.payload}');
    switch (frame.type) {
      case 'hello':
        // 握手：回 welcome（版本 + caps + 当前全量 scene）
        _tcpServer.reply(jsonEncode({
          'server': 'jarvis-overlay',
          'version': 1,
          'caps': _sceneCaps,
          'scene': _scene,
        }));
        break;
      case 'scene':
        if (frame.payload is Map) {
          _applyScene(Map<String, dynamic>.from(frame.payload as Map));
        }
        break;
      case 'patch':
        if (frame.payload is Map) {
          _applyScene(Map<String, dynamic>.from(frame.payload as Map));
        }
        break;
      case 'welcome':
        // 前端一般不主动收 welcome（后端 client 发 hello）；收到则忽略
        break;
    }
  }

  /// 把 SCENE 帧（全量/增量同构）合并进 scene 状态并投影到当前 agent。
  /// scene/patch 都是「字段 → 值」映射；patch 只含变化字段，合并后渲染。
  void _applyScene(Map<String, dynamic> patch) {
    if (patch.containsKey('agent')) {
      final agentName = patch['agent'].toString().trim();
      if (agentName.isNotEmpty &&
          _agents.containsKey(agentName) &&
          agentName != _currentAgentName) {
        setState(() {
          _agents[_currentAgentName]!.handleCommand('hide');
          _currentAgentName = agentName;
        });
      }
    }
    if (patch.containsKey('effect')) {
      _agents[_currentAgentName]!.handleCommand(patch['effect'].toString());
    }
    if (patch.containsKey('user') && patch['user'] is Map) {
      final u = Map<String, dynamic>.from(patch['user'] as Map);
      final text = (u['text'] ?? '').toString();
      _agents[_currentAgentName]!.handleCommand('user:$text');
    }
    if (patch.containsKey('ai') && patch['ai'] is Map) {
      final a = Map<String, dynamic>.from(patch['ai'] as Map);
      final text = (a['text'] ?? '').toString();
      _agents[_currentAgentName]!.handleCommand('ai:$text');
    }
    if (patch.containsKey('monitor') && patch['monitor'] is Map) {
      _agents[_currentAgentName]!.handleCommand(
        'monitor:${jsonEncode(patch['monitor'])}',
      );
    }
    if (patch.containsKey('tool_call')) {
      final text = patch['tool_call'].toString();
      if (text.isEmpty) {
        _agents[_currentAgentName]!.handleCommand('tool_call_end:');
      } else {
        _agents[_currentAgentName]!.handleCommand('tool_call:$text');
      }
    }
    // 合并进 scene 状态（供 hello 握手回传全量）
    _scene = {..._scene, ...patch};
  }

  void _handleCommand(String command) {
    print('[AgentOverlay] Received command: $command');

    if (command == 'wake') {
      final now = DateTime.now();
      if (_lastWakeTime != null &&
          now.difference(_lastWakeTime!).inMilliseconds < _wakeCooldownMs) {
        print('[AgentOverlay] Ignoring duplicate wake command');
        return;
      }
      _lastWakeTime = now;
    }

    if (command.startsWith('agent:')) {
      final agentName = command.substring(6).trim();
      print(
        '[AgentOverlay] Agent switch request: $agentName, current: $_currentAgentName',
      );
      if (agentName != _currentAgentName && _agents.containsKey(agentName)) {
        print('[AgentOverlay] Switching to agent: $agentName');
        setState(() {
          _agents[_currentAgentName]!.handleCommand('hide');
          _currentAgentName = agentName;
        });
        print('[AgentOverlay] Switched to Agent: $agentName');
      } else if (!_agents.containsKey(agentName)) {
        print('[AgentOverlay] ERROR: Unknown agent: $agentName');
      } else {
        print('[AgentOverlay] Already on agent: $agentName');
      }
      return;
    }

    print('[AgentOverlay] Dispatching to $_currentAgentName: $command');
    setState(() {
      _agents[_currentAgentName]!.handleCommand(command);
    });
  }

  @override
  void dispose() {
    for (final agent in _agents.values) {
      agent.dispose();
    }
    _tcpServer.stop();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final screenWidth = MediaQuery.of(context).size.width;
    final screenHeight = widget.screenHeight;

    final agentList = _agents.entries.toList();
    final currentIndex = agentList.indexWhere(
      (e) => e.key == _currentAgentName,
    );

    return IndexedStack(
      index: currentIndex,
      sizing: StackFit.expand,
      children: agentList.map((entry) {
        final agent = entry.value;
        return Stack(
          children: [
            agent.buildAiTerminal(context, screenWidth, screenHeight),
            agent.buildUserTerminal(context, screenWidth, screenHeight),
            agent.buildEffects(context, screenWidth, screenHeight),
            agent.buildOtherOne(context, screenWidth, screenHeight),
            agent.buildOtherTwo(context, screenWidth, screenHeight)
          ],
        );
      }).toList(),
    );
  }
}
