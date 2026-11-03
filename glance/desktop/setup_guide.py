"""Setup walkthrough hosted inside About."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from .settings_widgets import SettingsGroup, SettingsRow


class SetupGuide(QScrollArea):
    section_titles = [
        "Before you begin", "Install face models", "Set up your Windows password",
        "Enroll your face", "Choose who may unlock", "Enable protection",
        "Privacy, security, and troubleshooting",
    ]

    def __init__(self, navigate: Callable[[int], None], open_models: Callable[[], None],
                 open_camera_privacy: Callable[[], None], return_to_about: Callable[[], None],
                 open_logs: Callable[[], None], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        content = QWidget()
        self.setWidget(content)
        self.action_buttons: dict[str, QPushButton] = {}
        root = QVBoxLayout(content)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        header = QHBoxLayout()
        self.back_button = QPushButton("← About")
        self.back_button.setAccessibleName("Return to About")
        self.back_button.clicked.connect(return_to_about)
        header.addWidget(self.back_button)
        title = QLabel("Complete setup guide")
        title.setObjectName("h1")
        header.addWidget(title, 1)
        root.addLayout(header)
        intro = QLabel("Set up face unlock at your own pace. This guide stays available from About.")
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        root.addWidget(intro)

        sections = [
            ("Before you begin", "Glance requires Windows 11 x64 and a webcam. Allow camera access in Windows Settings > Privacy & security > Camera. Keep your Windows PIN and password available; Glance is a convenience feature, not a replacement for Windows Hello."),
            ("Install face models", "Download the InsightFace buffalo_s models when prompted. Glance verifies the downloaded files and stores them locally. Model terms and the existing download flow are shown before installation."),
            ("Set up your Windows password", "Use the account name Windows accepts at sign-in; a Windows Hello PIN is not a password. Local accounts use COMPUTERNAME\\username (or .\\username); Microsoft accounts use MicrosoftAccount\\your-email. Check the detected account type, then verify and save the password in the Password screen. It is encrypted with machine-scoped DPAPI, but processes running on this PC can decrypt it while you are signed in. If Windows is set to Hello-only sign-in, allow password sign-in in Windows Sign-in options."),
            ("Enroll your face", "Choose Your Face, enter a name, and start guided capture. Follow the straight-ahead and slight left, right, up, and down prompts until 18 quality-checked samples are collected. Glance keeps face embeddings, not camera images. You can recapture or remove an identity later."),
            ("Choose who may unlock", "In Recognition, explicitly select one enrolled person to allow. Keep that selection enabled and verify the displayed status. Missing or invalid selections must not authorize another person."),
            ("Enable protection", "Use General to enable or resume background protection and review its status. Windows may request administrator approval to enable its credential provider. Your normal Windows password and PIN sign-in options remain available."),
            ("Privacy, security, and troubleshooting", "Face matching and motion/anti-spoof checks run on-device. Ordinary RGB webcams cannot prove a live person; a moving photo or replay video may pass, and false denials can occur. If camera, models, or account checks fail, use Windows PIN/password sign-in, check Camera settings, confirm the password/account, review About > Diagnostics, and restart protection if needed."),
        ]
        self._add_section(root, *sections[0])
        self._action(root, "Camera privacy settings", open_camera_privacy)
        self._add_section(root, *sections[1])
        self._action(root, "Models", open_models)
        self._add_section(root, *sections[2])
        self._action(root, "Password", lambda: navigate(2))
        self._add_section(root, *sections[3])
        self._action(root, "Your Face", lambda: navigate(1))
        self._add_section(root, *sections[4])
        self._action(root, "Recognition", lambda: navigate(4))
        self._add_section(root, *sections[5])
        self._action(root, "General", lambda: navigate(0))
        self._add_section(root, *sections[6])
        self._action(root, "Diagnostics", open_logs)
        root.addStretch(1)

    @staticmethod
    def _add_section(root: QVBoxLayout, title: str, body: str) -> None:
        group = SettingsGroup()
        group.add_row(SettingsRow(title, body))
        root.addWidget(group)

    def _action(self, root: QVBoxLayout, label: str, callback: Callable[[], None]) -> None:
        button = QPushButton(label)
        button.clicked.connect(callback)
        self.action_buttons[label] = button
        root.addWidget(button)
