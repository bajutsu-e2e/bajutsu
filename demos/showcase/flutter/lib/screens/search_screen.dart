import 'package:flutter/material.dart';

import '../accessibility.dart';
import '../model.dart';

/// Tab: Search (SPEC §5.2). Filters the shared catalog by name, case-insensitive.
class SearchScreen extends StatefulWidget {
  const SearchScreen({super.key, required this.model});

  final AppModel model;

  @override
  State<SearchScreen> createState() => _SearchScreenState();
}

class _SearchScreenState extends State<SearchScreen> {
  late final TextEditingController _controller = TextEditingController(text: widget.model.searchQuery);

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final model = widget.model;
    final matches = model.horsesMatching(model.searchQuery);
    return Scaffold(
      appBar: AppBar(title: const Text('Search')),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.all(16),
            child: Row(
              children: [
                Expanded(
                  child: aid(
                    'search.field',
                    TextField(
                      controller: _controller,
                      onChanged: (v) => model.searchQuery = v,
                      decoration: const InputDecoration(labelText: 'Search horses'),
                    ),
                  ),
                ),
                aid(
                  'search.clear',
                  TextButton(
                    onPressed: () {
                      _controller.clear();
                      model.searchQuery = '';
                    },
                    child: const Text('Clear'),
                  ),
                ),
              ],
            ),
          ),
          aidValue(
            'search.count',
            '${matches.length}',
            Padding(padding: const EdgeInsets.symmetric(horizontal: 16), child: Text('Matches: ${matches.length}')),
          ),
          Expanded(
            child: matches.isEmpty
                ? aid('search.results-empty', const Padding(padding: EdgeInsets.all(16), child: Text('No matches')))
                : ListView(
                    children: [
                      for (final horse in matches)
                        aid('search.row.${horse.id}', ListTile(title: Text(horse.name))),
                    ],
                  ),
          ),
        ],
      ),
    );
  }
}
