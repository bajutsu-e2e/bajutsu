import UIKit

/// Tab: Log (SPEC §5.3) — a training-log composer that exercises every input control
/// and all four modal presentation styles (sheet, full-screen cover, action sheet,
/// transient toast).
final class LogController: UIViewController, UITableViewDataSource {
    private let model: AppModel

    private let noteField = UITextField()
    private let stepper = UIStepper()
    private let countLabel = UILabel()
    private let intenseSwitch = UISwitch()
    private let statusLabel = UILabel()
    private let dialogResultLabel = UILabel()
    private let tableView = UITableView(frame: .zero, style: .plain)

    private var entries: [String] = []
    private var count = 0

    init(model: AppModel) {
        self.model = model
        super.init(nibName: nil, bundle: nil)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemBackground
        title = "Log"
        navigationItem.titleView = makeTitleView("Log").accessibilityID("log.title")

        noteField.placeholder = "Note"
        noteField.borderStyle = .roundedRect
        noteField.accessibilityID("log.note")

        stepper.minimumValue = 0
        stepper.maximumValue = 99
        stepper.addAction(UIAction { [weak self] _ in self?.stepperChanged() }, for: .valueChanged)
        stepper.accessibilityID("log.count")

        countLabel.text = "Count: 0"
        updateCountMirror()

        intenseSwitch.addAction(UIAction { [weak self] _ in self?.intenseChanged() }, for: .valueChanged)
        intenseSwitch.accessibilityID("log.intense")
        updateIntenseMirror()
        let intenseLabel = UILabel()
        intenseLabel.text = "Intense"
        let intenseRow = UIStackView(arrangedSubviews: [intenseLabel, intenseSwitch])
        intenseRow.spacing = 12

        let stepperRow = UIStackView(arrangedSubviews: [countLabel, stepper])
        stepperRow.spacing = 12

        let submit = UIButton(type: .system, primaryAction: UIAction(title: "Submit") { [weak self] _ in
            self?.submit()
        })
        submit.configuration = .borderedProminent()
        submit.accessibilityID("log.submit")

        statusLabel.font = .preferredFont(forTextStyle: .footnote)
        statusLabel.textColor = .secondaryLabel
        statusLabel.accessibilityID("log.status")
        setStatus(.idle)

        // The four modal triggers.
        let filter = makeModalButton("Filter (sheet)", "log.openFilter") { [weak self] in self?.openFilter() }
        let gallery = makeModalButton("Gallery (cover)", "log.openGallery") { [weak self] in self?.openGallery() }
        let delete = makeModalButton("Delete…", "log.openDelete") { [weak self] in self?.openDelete() }

        dialogResultLabel.font = .preferredFont(forTextStyle: .footnote)
        dialogResultLabel.textColor = .secondaryLabel
        dialogResultLabel.text = "Choice: none"
        dialogResultLabel.accessibilityID("log.dialog.value")
        dialogResultLabel.accessibilityStateValue("none")

        tableView.dataSource = self
        tableView.translatesAutoresizingMaskIntoConstraints = false

        let form = UIStackView(arrangedSubviews: [
            noteField, stepperRow, intenseRow, submit, statusLabel,
            filter, gallery, delete, dialogResultLabel,
        ])
        form.axis = .vertical
        form.spacing = 14
        form.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(form)
        view.addSubview(tableView)

        let guide = view.safeAreaLayoutGuide
        NSLayoutConstraint.activate([
            form.topAnchor.constraint(equalTo: guide.topAnchor, constant: 16),
            form.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 20),
            form.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -20),

            tableView.topAnchor.constraint(equalTo: form.bottomAnchor, constant: 12),
            tableView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            tableView.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            tableView.bottomAnchor.constraint(equalTo: guide.bottomAnchor),
        ])
    }

    private func makeModalButton(_ title: String, _ id: String, _ action: @escaping () -> Void) -> UIButton {
        let button = UIButton(type: .system, primaryAction: UIAction(title: title) { _ in action() })
        button.configuration = .bordered()
        button.accessibilityID(id)
        return button
    }

    // MARK: - Controls

    private func stepperChanged() {
        count = Int(stepper.value)
        countLabel.text = "Count: \(count)"
        updateCountMirror()
    }

    private func updateCountMirror() {
        countLabel.accessibilityStateValue(String(count))
    }

    private func intenseChanged() {
        updateIntenseMirror()
    }

    private func updateIntenseMirror() {
        intenseSwitch.accessibilityStateValue(intenseSwitch.isOn ? "on" : "off")
    }

    private func setStatus(_ status: ShowcaseNet.Status) {
        statusLabel.text = "Status: \(status.rawValue)"
        statusLabel.accessibilityStateValue(status.rawValue)
    }

    // MARK: - Submit (networking + toast)

    private func submit() {
        let note = noteField.text ?? ""
        let body = #"{"note":"\#(note)","count":\#(count)}"#
        ShowcaseNet.request("POST", "\(model.httpBase)/post", body: body) { [weak self] status in
            guard let self else { return }
            setStatus(status)
            if status == .done {
                entries.append(note.isEmpty ? "Entry \(entries.count + 1)" : note)
                tableView.reloadData()
                showToast()
            }
        }
    }

    /// Transient toast that auto-dismisses ~1.2 s — exercises `wait until gone` (SPEC §5.3).
    private func showToast() {
        let toast = UILabel()
        toast.text = "Logged"
        toast.textColor = .white
        toast.textAlignment = .center
        toast.backgroundColor = UIColor.label.withAlphaComponent(0.85)
        toast.layer.cornerRadius = 10
        toast.layer.masksToBounds = true
        toast.accessibilityID("log.toast")
        toast.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(toast)
        NSLayoutConstraint.activate([
            toast.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            toast.bottomAnchor.constraint(equalTo: view.safeAreaLayoutGuide.bottomAnchor, constant: -24),
            toast.widthAnchor.constraint(equalToConstant: 160),
            toast.heightAnchor.constraint(equalToConstant: 44),
        ])
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) {
            toast.removeFromSuperview()
        }
    }

    // MARK: - Modals (the four styles)

    /// 1) Detented sheet.
    private func openFilter() {
        let sheet = FilterSheetController()
        sheet.modalPresentationStyle = .pageSheet
        if let presentation = sheet.sheetPresentationController {
            presentation.detents = [.medium(), .large()]
            presentation.prefersGrabberVisible = true
        }
        present(sheet, animated: !model.animationsDisabled)
    }

    /// 2) Full-screen cover.
    private func openGallery() {
        let cover = GalleryCoverController()
        cover.modalPresentationStyle = .fullScreen
        present(cover, animated: !model.animationsDisabled)
    }

    /// 3) Action sheet (UIAlertController .actionSheet) with a destructive choice.
    private func openDelete() {
        let alert = UIAlertController(title: "Delete entry?", message: nil, preferredStyle: .actionSheet)
        let archive = UIAlertAction(title: "Archive", style: .default) { [weak self] _ in
            self?.setDialogResult("archive")
        }
        archive.accessibilityID("log.dialog.archive")
        let delete = UIAlertAction(title: "Delete", style: .destructive) { [weak self] _ in
            self?.setDialogResult("delete")
        }
        delete.accessibilityID("log.dialog.delete")
        let cancel = UIAlertAction(title: "Cancel", style: .cancel)
        cancel.accessibilityID("log.dialog.cancel")
        alert.addAction(archive)
        alert.addAction(delete)
        alert.addAction(cancel)
        // iPad: anchor the popover to the trigger.
        if let pop = alert.popoverPresentationController {
            pop.sourceView = view
            pop.sourceRect = CGRect(x: view.bounds.midX, y: view.bounds.midY, width: 0, height: 0)
        }
        present(alert, animated: !model.animationsDisabled)
    }

    private func setDialogResult(_ value: String) {
        dialogResultLabel.text = "Choice: \(value)"
        dialogResultLabel.accessibilityStateValue(value)
    }

    // MARK: - Table

    func tableView(_ tableView: UITableView, numberOfRowsInSection section: Int) -> Int {
        entries.count
    }

    func tableView(_ tableView: UITableView, cellForRowAt indexPath: IndexPath) -> UITableViewCell {
        let cell = UITableViewCell(style: .default, reuseIdentifier: nil)
        cell.textLabel?.text = entries[indexPath.row]
        cell.accessibilityID("log.row.\(indexPath.row + 1)")  // 1-based entry index (SPEC §5.3)
        return cell
    }
}
