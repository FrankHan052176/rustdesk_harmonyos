import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../common.dart';
import '../../models/platform_model.dart';
import '../../models/server_model.dart';

/// Compact incoming-client list for HarmonyOS desktop/2in1 layout (HarmonyOS
/// has no separate `--cm` process window). Service start/stop stays on the
/// standard bottom status bar and settings page.
class OhosHostClientList extends StatelessWidget {
  const OhosHostClientList({super.key});

  @override
  Widget build(BuildContext context) {
    final serverModel = Provider.of<ServerModel>(context);
    final clients = serverModel.clients;
    if (clients.isEmpty) {
      return const SizedBox.shrink();
    }
    return Container(
      margin: const EdgeInsets.only(left: 20, right: 11, top: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: clients
            .map((client) => Container(
                  margin: const EdgeInsets.only(bottom: 6),
                  padding:
                      const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
                  decoration: BoxDecoration(
                    color: Theme.of(context).colorScheme.surface,
                    borderRadius: BorderRadius.circular(6),
                    border: Border.all(
                        color: Theme.of(context).dividerColor.withOpacity(0.5)),
                  ),
                  child: Row(
                    children: [
                      Expanded(
                        child: Text(
                          client.name.isNotEmpty ? client.name : client.peerId,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(fontSize: 12),
                        ),
                      ),
                      if (!client.authorized &&
                          serverModel.approveMode != 'password')
                        IconButton(
                          padding: EdgeInsets.zero,
                          constraints: const BoxConstraints(
                              minWidth: 24, minHeight: 24),
                          icon: const Icon(Icons.check,
                              size: 16, color: Colors.green),
                          tooltip: translate('Accept'),
                          onPressed: () =>
                              serverModel.sendLoginResponse(client, true),
                        ),
                      IconButton(
                        padding: EdgeInsets.zero,
                        constraints:
                            const BoxConstraints(minWidth: 24, minHeight: 24),
                        icon: const Icon(Icons.close,
                            size: 16, color: Colors.redAccent),
                        tooltip: translate(
                            client.authorized ? 'Disconnect' : 'Dismiss'),
                        onPressed: () => client.authorized
                            ? bind.cmCloseConnection(connId: client.id)
                            : serverModel.sendLoginResponse(client, false),
                      ),
                    ],
                  ),
                ))
            .toList(),
      ),
    );
  }
}
