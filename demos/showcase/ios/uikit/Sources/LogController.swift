import UIKit

/// Tab: Log (SPEC §5.3) — a training-log composer that exercises every input control and all
/// four modal presentation styles (sheet, full-screen cover, action sheet, transient toast).
/// Laid out as a grouped form (Entry / Modals / Gestures / Entries) to mirror the SwiftUI twin.
final class LogController: UIViewController {
    private let model: AppModel

    private let noteView = UITextView()
    private let stepper = UIStepper()
    private let countLabel = UILabel()
    private let intenseButton = UIButton(type: .system)
    private let intenseValueLabel = UILabel()
    private let segmentValueLabel = UILabel()
    private let statusLabel = UILabel()
    private let dialogResultLabel = UILabel()
    private let longPressTarget = UILabel()
    private let longPressValueLabel = UILabel()
    private let doubleTapTarget = UILabel()
    private let doubleTapValueLabel = UILabel()
    private let entriesStack = UIStackView()

    private var count = 0
    private var intense = false
    private var segment = "one"
    private var entryCount = 0
    private var doubleTaps = 0
    private var dialogOverlay: UIView?

    // The segmented control's choices, in display order.
    private let segments = ["one", "two", "three"]
    private var segmentButtons: [String: UIButton] = [:]

    init(model: AppModel) {
        self.model = model
        super.init(nibName: nil, bundle: nil)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func viewDidLoad() {
        super.viewDidLoad()
        title = "Log"

        // Multiline note (matches the SwiftUI vertical-axis TextField). ASCII keyboard + no
        // autocorrect so an active IME does not mangle typed text.
        noteView.font = .preferredFont(forTextStyle: .body)
        noteView.autocorrectionType = .no
        noteView.keyboardType = .asciiCapable
        noteView.backgroundColor = .clear
        noteView.accessibilityID("log.note")
        noteView.translatesAutoresizingMaskIntoConstraints = false
        noteView.heightAnchor.constraint(equalToConstant: 72).isActive = true

        stepper.minimumValue = 0
        stepper.maximumValue = 99
        stepper.addAction(UIAction { [weak self] _ in self?.stepperChanged() }, for: .valueChanged)
        stepper.accessibilityID("log.count")
        countLabel.accessibilityID("log.count.value")
        updateCountMirror()
        let stepperRow = makeRow(countLabel, stepper)

        // A button-backed toggle (not a UISwitch — the retired idb backend couldn't flip one on iOS 26, BE-0290) shown as a
        // checkbox, the same look as the SwiftUI Label("Intense", systemImage: "checkmark.square").
        intenseButton.setTitle(" Intense", for: .normal)
        intenseButton.contentHorizontalAlignment = .leading
        intenseButton.addAction(UIAction { [weak self] _ in self?.toggleIntense() }, for: .primaryActionTriggered)
        intenseButton.accessibilityID("log.intense")
        intenseValueLabel.font = .preferredFont(forTextStyle: .footnote)
        intenseValueLabel.textColor = .secondaryLabel
        intenseValueLabel.accessibilityID("log.intense.value")
        updateIntenseMirror()

        let segmentRow = makeSegmentedControl()
        segmentValueLabel.font = .preferredFont(forTextStyle: .footnote)
        segmentValueLabel.textColor = .secondaryLabel
        segmentValueLabel.accessibilityID("log.segment.value")
        updateSegmentMirror()

        let submit = UIButton(type: .system, primaryAction: UIAction(title: "Submit") { [weak self] _ in
            self?.submit()
        })
        submit.contentHorizontalAlignment = .leading
        submit.accessibilityID("log.submit")

        statusLabel.font = .preferredFont(forTextStyle: .footnote)
        statusLabel.textColor = .secondaryLabel
        statusLabel.accessibilityID("log.status")
        setStatus(.idle)

        let filter = makeModalButton("Open Filter", "log.openFilter") { [weak self] in self?.openFilter() }
        let gallery = makeModalButton("Open Gallery", "log.openGallery") { [weak self] in self?.openGallery() }
        let delete = makeModalButton("Open Delete", "log.openDelete") { [weak self] in self?.openDelete() }

        dialogResultLabel.font = .preferredFont(forTextStyle: .footnote)
        dialogResultLabel.textColor = .secondaryLabel
        dialogResultLabel.text = "Dialog: none"
        dialogResultLabel.accessibilityID("log.dialog.value")
        dialogResultLabel.accessibilityStateValue("none")

        configureGestureTargets()
        entriesStack.axis = .vertical
        entriesStack.spacing = 12

        installGroupedForm([
            makeSectionHeader("Entry"),
            makeSectionCard([noteView, stepperRow, intenseButton, intenseValueLabel, submit, statusLabel]),
            makeSectionHeader("Modals"),
            makeSectionCard([filter, gallery, delete, dialogResultLabel]),
            makeSectionHeader("Gestures"),
            makeSectionCard([longPressTarget, longPressValueLabel, doubleTapTarget, doubleTapValueLabel]),
            makeSectionHeader("Controls"),
            makeSectionCard([segmentRow, segmentValueLabel]),
            makeSectionHeader("Entries"),
            makeSectionCard([entriesStack]),
        ])
    }

    /// A label-then-control horizontal row (the label stretches; the control hugs its content).
    private func makeRow(_ label: UIView, _ control: UIView) -> UIStackView {
        let row = UIStackView(arrangedSubviews: [label, control])
        row.axis = .horizontal
        row.spacing = 12
        row.alignment = .center
        label.setContentHuggingPriority(.defaultLow, for: .horizontal)
        control.setContentHuggingPriority(.required, for: .horizontal)
        return row
    }

    private func makeModalButton(_ title: String, _ id: String, _ action: @escaping () -> Void) -> UIButton {
        let button = UIButton(type: .system, primaryAction: UIAction(title: title) { _ in action() })
        button.contentHorizontalAlignment = .leading
        button.accessibilityID(id)
        return button
    }

    // MARK: - Gesture targets (SPEC §5.3)

    // A long-press and a double-tap target whose results mirror to a11y values, so a scenario
    // can assert the gesture landed. The mirror starts at idle / 0.
    private func configureGestureTargets() {
        longPressTarget.text = "Long-press me"
        longPressTarget.isUserInteractionEnabled = true
        longPressTarget.accessibilityID("log.longpress")
        let longPress = UILongPressGestureRecognizer(target: self, action: #selector(longPressed))
        longPress.minimumPressDuration = 0.5
        longPressTarget.addGestureRecognizer(longPress)

        longPressValueLabel.font = .preferredFont(forTextStyle: .footnote)
        longPressValueLabel.textColor = .secondaryLabel
        longPressValueLabel.text = "idle"
        longPressValueLabel.accessibilityID("log.longpress.value")
        longPressValueLabel.accessibilityStateValue("idle")

        doubleTapTarget.text = "Double-tap me"
        doubleTapTarget.isUserInteractionEnabled = true
        doubleTapTarget.accessibilityID("log.doubletap")
        let doubleTap = UITapGestureRecognizer(target: self, action: #selector(doubleTapped))
        doubleTap.numberOfTapsRequired = 2
        doubleTapTarget.addGestureRecognizer(doubleTap)

        doubleTapValueLabel.font = .preferredFont(forTextStyle: .footnote)
        doubleTapValueLabel.textColor = .secondaryLabel
        doubleTapValueLabel.text = "Double-taps: 0"
        doubleTapValueLabel.accessibilityID("log.doubletap.value")
        doubleTapValueLabel.accessibilityStateValue("0")
    }

    @objc private func longPressed(_ gesture: UILongPressGestureRecognizer) {
        guard gesture.state == .began else { return }  // fire once, on the press onset
        longPressValueLabel.text = "pressed"
        longPressValueLabel.accessibilityStateValue("pressed")
    }

    @objc private func doubleTapped() {
        doubleTaps += 1
        doubleTapValueLabel.text = "Double-taps: \(doubleTaps)"
        doubleTapValueLabel.accessibilityStateValue(String(doubleTaps))
    }

    // MARK: - Controls

    private func stepperChanged() {
        count = Int(stepper.value)
        updateCountMirror()
    }

    private func updateCountMirror() {
        countLabel.text = "Count: \(count)"
        countLabel.accessibilityStateValue(String(count))
    }

    private func toggleIntense() {
        intense.toggle()
        updateIntenseMirror()
    }

    private func updateIntenseMirror() {
        intenseButton.setImage(UIImage(systemName: intense ? "checkmark.square.fill" : "square"), for: .normal)
        intenseButton.accessibilityTraits = intense ? [.button, .selected] : [.button]
        intenseValueLabel.text = intense ? "Intense" : "Easy"
        intenseValueLabel.accessibilityStateValue(intense ? "on" : "off")
    }

    // A button-backed segmented control (not a UISegmentedControl — the retired idb backend's tap did not switch a
    // native one on iOS 26, BE-0290). Each choice is a checkable button whose `selected` trait reflects the
    // current pick, the same idiom as the Intense toggle; the selection mirrors to log.segment.value.
    private func makeSegmentedControl() -> UIStackView {
        let row = UIStackView()
        row.axis = .horizontal
        row.spacing = 12
        row.distribution = .fillEqually
        for choice in segments {
            let button = UIButton(type: .system, primaryAction: UIAction(title: choice.capitalized) { [weak self] _ in
                self?.selectSegment(choice)
            })
            button.accessibilityID("log.segment.\(choice)")
            segmentButtons[choice] = button
            row.addArrangedSubview(button)
        }
        return row
    }

    private func selectSegment(_ choice: String) {
        segment = choice
        updateSegmentMirror()
    }

    private func updateSegmentMirror() {
        for (choice, button) in segmentButtons {
            button.accessibilityTraits = choice == segment ? [.button, .selected] : [.button]
        }
        segmentValueLabel.text = "Segment: \(segment)"
        segmentValueLabel.accessibilityStateValue(segment)
    }

    private func setStatus(_ status: ShowcaseNet.Status) {
        statusLabel.text = "Status: \(status.rawValue)"
        statusLabel.accessibilityStateValue(status.rawValue)
    }

    // MARK: - Submit (networking + toast)

    private func submit() {
        let note = noteView.text ?? ""
        let body = #"{"note":"\#(note)","count":\#(count)}"#
        ShowcaseNet.request("POST", "\(model.httpBase)/post", body: body) { [weak self] status in
            guard let self else { return }
            setStatus(status)
            if status == .done {
                entryCount += 1
                let label = UILabel()
                label.text = note.isEmpty ? "Entry \(entryCount)" : note
                label.accessibilityID("log.row.\(entryCount)")  // 1-based entry index (SPEC §5.3)
                entriesStack.addArrangedSubview(label)
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

    /// 3) Action sheet: a custom overlay of plain buttons (not UIAlertController, whose action
    /// buttons the retired idb backend could not reliably drive on iOS 26, BE-0290). Plain UIButtons resolve uniquely; result
    /// mirrors to log.dialog.value.
    private func openDelete() {
        let titleLabel = UILabel()
        titleLabel.text = "Delete entry"
        titleLabel.font = .preferredFont(forTextStyle: .headline)
        titleLabel.accessibilityID("log.dialog.title")

        let archive = makeDialogButton("Archive", "log.dialog.archive") { [weak self] in self?.setDialogResult("archive") }
        let del = makeDialogButton("Delete", "log.dialog.delete") { [weak self] in self?.setDialogResult("delete") }
        let cancel = makeDialogButton("Cancel", "log.dialog.cancel") { [weak self] in self?.dismissDialog() }

        let card = UIStackView(arrangedSubviews: [titleLabel, archive, del, cancel])
        card.axis = .vertical
        card.spacing = 16
        card.alignment = .center
        card.isLayoutMarginsRelativeArrangement = true
        card.layoutMargins = UIEdgeInsets(top: 24, left: 32, bottom: 24, right: 32)
        card.backgroundColor = .secondarySystemBackground
        card.layer.cornerRadius = 16
        card.translatesAutoresizingMaskIntoConstraints = false

        let backdrop = UIView()
        backdrop.backgroundColor = UIColor.black.withAlphaComponent(0.2)
        backdrop.translatesAutoresizingMaskIntoConstraints = false
        backdrop.addSubview(card)
        view.addSubview(backdrop)
        NSLayoutConstraint.activate([
            backdrop.topAnchor.constraint(equalTo: view.topAnchor),
            backdrop.bottomAnchor.constraint(equalTo: view.bottomAnchor),
            backdrop.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            backdrop.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            card.centerXAnchor.constraint(equalTo: backdrop.centerXAnchor),
            card.centerYAnchor.constraint(equalTo: backdrop.centerYAnchor),
        ])
        dialogOverlay = backdrop
    }

    private func makeDialogButton(_ title: String, _ id: String, _ action: @escaping () -> Void) -> UIButton {
        let button = UIButton(type: .system, primaryAction: UIAction(title: title) { _ in action() })
        button.accessibilityID(id)
        return button
    }

    private func setDialogResult(_ value: String) {
        dialogResultLabel.text = "Dialog: \(value)"
        dialogResultLabel.accessibilityStateValue(value)
        dismissDialog()
    }

    private func dismissDialog() {
        dialogOverlay?.removeFromSuperview()
        dialogOverlay = nil
    }
}
