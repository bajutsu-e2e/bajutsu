import 'package:flutter/material.dart';

import '../accessibility.dart';
import '../model.dart';
import '../net.dart';
import 'common.dart';

/// Tab: Stable (SPEC §5.1). A catalog list with async load; tapping a row pushes Horse Detail on the
/// root navigator. The nav-bar title carries no id — a scenario confirms the screen via a content
/// leaf (`stable.row.1` / `stable.status`).
class StableScreen extends StatelessWidget {
  const StableScreen({super.key, required this.model});

  final AppModel model;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Stable'),
        actions: [
          aid(
            'stable.refresh',
            TextButton(
              onPressed: () async {
                model.stableStatus = 'loading';
                model.stableStatus = await Net.get('${model.apiURL}/horses');
              },
              child: const Text('Refresh'),
            ),
          ),
        ],
      ),
      body: Column(
        children: [
          Expanded(
            child: model.horses.isEmpty
                ? aid('stable.empty', const Padding(padding: EdgeInsets.all(16), child: Text('No horses')))
                : ListView(
                    children: [
                      for (final horse in model.horses)
                        aid(
                          'stable.row.${horse.id}',
                          ListTile(
                            title: Text(horse.name),
                            onTap: () {
                              model.resetHorseDetail();
                              Navigator.of(context).push(
                                MaterialPageRoute<void>(builder: (_) => HorseDetailScreen(model: model, id: horse.id)),
                              );
                            },
                          ),
                        ),
                    ],
                  ),
          ),
          // Status mirrors to stable.status so a scenario can wait on the response before asserting.
          aidValue(
            'stable.status',
            model.stableStatus,
            Padding(padding: const EdgeInsets.all(8), child: Text('Status: ${model.stableStatus}')),
          ),
        ],
      ),
    );
  }
}

/// Horse Detail, pushed by tapping a Stable row (SPEC §5.1). `horse.title` / `horse.id.value` are
/// real content (the entity), so they keep their ids even though the nav title does not.
class HorseDetailScreen extends StatelessWidget {
  const HorseDetailScreen({super.key, required this.model, required this.id});

  final AppModel model;
  final int id;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: model,
      builder: (context, _) {
        final horse = model.horse(id);
        return Scaffold(
          appBar: detailAppBar(horse?.name ?? 'Horse $id'),
          body: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                aid('horse.title', Text(horse?.name ?? 'Horse $id', style: Theme.of(context).textTheme.titleLarge)),
                aidValue('horse.id.value', '$id', Text('ID: $id')),
                aid(
                  'horse.fetch',
                  TextButton(
                    onPressed: () async {
                      model.horseStatus = 'loading';
                      model.horseStatus = await Net.get('${model.apiURL}/horses/$id');
                    },
                    child: const Text('Fetch detail'),
                  ),
                ),
                aidValue('horse.status', model.horseStatus, Text('Status: ${model.horseStatus}')),
                // A button-backed toggle; `selected` reflects the state, value mirrors on/off.
                aidSelected(
                  'horse.favorite',
                  model.horseFavorite,
                  TextButton(
                    onPressed: () => model.horseFavorite = !model.horseFavorite,
                    child: Text(model.horseFavorite ? '★ Favorite' : '☆ Favorite'),
                  ),
                ),
                aidValue(
                  'horse.favorite.value',
                  model.horseFavorite ? 'on' : 'off',
                  Text(model.horseFavorite ? 'Favorited' : 'Not favorited'),
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}
