import UIKit

/// Tab: Search (SPEC §5.2). A search field filters the same catalog by name, case-insensitive;
/// a count text mirrors the number of matches; an empty state shows when nothing matches. Laid
/// out to mirror the SwiftUI twin: a plain rounded text field + Clear, a centered match count,
/// and a grouped list.
final class SearchController: UIViewController, UITableViewDataSource, UITableViewDelegate {
    private let model: AppModel
    private var results: [Horse]

    private let searchField = UITextField()
    private let countLabel = UILabel()
    private let tableView = UITableView(frame: .zero, style: .insetGrouped)
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
        view.backgroundColor = .systemGroupedBackground
        title = "Search"

        searchField.placeholder = "Search horses"
        searchField.borderStyle = .roundedRect
        searchField.autocapitalizationType = .none
        // ASCII keyboard + no autocorrect so typed Latin text is not mangled by an active IME
        // (a Japanese keyboard turns "Horse 3" into romaji→kana).
        searchField.autocorrectionType = .no
        searchField.keyboardType = .asciiCapable
        searchField.clearButtonMode = .whileEditing
        searchField.accessibilityID("search.field")
        searchField.addAction(
            UIAction { [weak self] _ in self?.applyFilter(self?.searchField.text ?? "") }, for: .editingChanged)

        let clear = UIButton(type: .system, primaryAction: UIAction(title: "Clear") { [weak self] _ in
            self?.clearQuery()
        })
        clear.accessibilityID("search.clear")

        countLabel.font = .preferredFont(forTextStyle: .footnote)
        countLabel.textColor = .secondaryLabel
        countLabel.textAlignment = .center
        countLabel.accessibilityID("search.count")

        emptyLabel.text = "No matches"
        emptyLabel.textColor = .secondaryLabel
        emptyLabel.textAlignment = .center
        emptyLabel.accessibilityID("search.results-empty")
        emptyLabel.isHidden = true
        emptyLabel.translatesAutoresizingMaskIntoConstraints = false

        tableView.dataSource = self
        tableView.delegate = self
        tableView.backgroundColor = .clear
        tableView.translatesAutoresizingMaskIntoConstraints = false

        let searchRow = UIStackView(arrangedSubviews: [searchField, clear])
        searchRow.spacing = 8
        searchRow.alignment = .center
        searchRow.translatesAutoresizingMaskIntoConstraints = false
        countLabel.translatesAutoresizingMaskIntoConstraints = false

        view.addSubview(searchRow)
        view.addSubview(countLabel)
        view.addSubview(tableView)
        view.addSubview(emptyLabel)

        let guide = view.safeAreaLayoutGuide
        NSLayoutConstraint.activate([
            searchRow.topAnchor.constraint(equalTo: guide.topAnchor, constant: 8),
            searchRow.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 16),
            searchRow.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -16),

            countLabel.topAnchor.constraint(equalTo: searchRow.bottomAnchor, constant: 8),
            countLabel.centerXAnchor.constraint(equalTo: view.centerXAnchor),

            tableView.topAnchor.constraint(equalTo: countLabel.bottomAnchor, constant: 4),
            tableView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            tableView.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            tableView.bottomAnchor.constraint(equalTo: view.bottomAnchor),

            emptyLabel.centerXAnchor.constraint(equalTo: tableView.centerXAnchor),
            emptyLabel.centerYAnchor.constraint(equalTo: tableView.centerYAnchor),
        ])

        applyFilter("")
    }

    private func applyFilter(_ query: String) {
        results = query.isEmpty
            ? model.horses
            : model.horses.filter { $0.name.localizedCaseInsensitiveContains(query) }
        countLabel.text = "Matches: \(results.count)"
        countLabel.accessibilityStateValue(String(results.count))
        emptyLabel.isHidden = !(results.isEmpty && !query.isEmpty)
        tableView.reloadData()
    }

    private func clearQuery() {
        searchField.text = ""
        searchField.resignFirstResponder()
        applyFilter("")
    }

    // MARK: - Table

    func tableView(_ tableView: UITableView, numberOfRowsInSection section: Int) -> Int {
        results.count
    }

    func tableView(_ tableView: UITableView, cellForRowAt indexPath: IndexPath) -> UITableViewCell {
        let cell = UITableViewCell(style: .default, reuseIdentifier: nil)
        let horse = results[indexPath.row]
        cell.textLabel?.text = horse.name
        cell.accessibilityID("search.row.\(horse.id)")  // same id scheme as Stable, search. namespace
        return cell
    }
}
