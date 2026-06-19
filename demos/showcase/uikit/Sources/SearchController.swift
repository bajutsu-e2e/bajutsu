import UIKit

/// Tab: Search (SPEC §5.2). A search field filters the same catalog by name,
/// case-insensitive; a count text mirrors the number of matches; an empty state shows
/// when nothing matches.
final class SearchController: UIViewController, UITableViewDataSource, UITableViewDelegate, UISearchBarDelegate {
    private let model: AppModel
    private var results: [Horse]

    private let searchBar = UISearchBar()
    private let countLabel = UILabel()
    private let tableView = UITableView(frame: .zero, style: .plain)
    private let emptyLabel = UILabel()

    init(model: AppModel) {
        self.model = model
        results = model.horses
        super.init(nibName: nil, bundle: nil)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemBackground
        title = "Search"
        navigationItem.titleView = makeTitleView("Search").aid("search.title")

        searchBar.placeholder = "Filter horses"
        searchBar.delegate = self
        searchBar.showsCancelButton = false
        searchBar.autocapitalizationType = .none
        searchBar.searchTextField.aid("search.field")
        // The bar's built-in clear button doubles as the spec's clear control.
        searchBar.searchTextField.clearButtonMode = .whileEditing

        let clear = UIButton(type: .system, primaryAction: UIAction(title: "Clear") { [weak self] _ in
            self?.clearQuery()
        })
        clear.aid("search.clear")

        countLabel.font = .preferredFont(forTextStyle: .footnote)
        countLabel.textColor = .secondaryLabel
        countLabel.aid("search.count")

        emptyLabel.text = "No matches"
        emptyLabel.textColor = .secondaryLabel
        emptyLabel.textAlignment = .center
        emptyLabel.aid("search.results-empty")

        tableView.dataSource = self
        tableView.delegate = self
        tableView.translatesAutoresizingMaskIntoConstraints = false

        searchBar.translatesAutoresizingMaskIntoConstraints = false
        clear.translatesAutoresizingMaskIntoConstraints = false
        countLabel.translatesAutoresizingMaskIntoConstraints = false

        view.addSubview(searchBar)
        view.addSubview(clear)
        view.addSubview(countLabel)
        view.addSubview(tableView)

        let guide = view.safeAreaLayoutGuide
        NSLayoutConstraint.activate([
            searchBar.topAnchor.constraint(equalTo: guide.topAnchor),
            searchBar.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            searchBar.trailingAnchor.constraint(equalTo: clear.leadingAnchor, constant: -8),

            clear.centerYAnchor.constraint(equalTo: searchBar.centerYAnchor),
            clear.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -16),

            countLabel.topAnchor.constraint(equalTo: searchBar.bottomAnchor, constant: 4),
            countLabel.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 16),

            tableView.topAnchor.constraint(equalTo: countLabel.bottomAnchor, constant: 4),
            tableView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            tableView.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            tableView.bottomAnchor.constraint(equalTo: guide.bottomAnchor),
        ])

        applyFilter("")
    }

    private func applyFilter(_ query: String) {
        results = query.isEmpty
            ? model.horses
            : model.horses.filter { $0.name.localizedCaseInsensitiveContains(query) }
        countLabel.text = "\(results.count) matches"
        countLabel.mirror(value: String(results.count))
        tableView.backgroundView = (results.isEmpty && !query.isEmpty) ? emptyLabel : nil
        tableView.reloadData()
    }

    private func clearQuery() {
        searchBar.text = ""
        searchBar.resignFirstResponder()
        applyFilter("")
    }

    // MARK: - UISearchBarDelegate

    func searchBar(_ searchBar: UISearchBar, textDidChange searchText: String) {
        applyFilter(searchText)
    }

    // MARK: - Table

    func tableView(_ tableView: UITableView, numberOfRowsInSection section: Int) -> Int {
        results.count
    }

    func tableView(_ tableView: UITableView, cellForRowAt indexPath: IndexPath) -> UITableViewCell {
        let cell = UITableViewCell(style: .default, reuseIdentifier: nil)
        let horse = results[indexPath.row]
        cell.textLabel?.text = horse.name
        cell.aid("search.row.\(horse.id)")  // same id scheme as Stable, search. namespace
        return cell
    }
}
