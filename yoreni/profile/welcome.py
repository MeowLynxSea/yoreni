"""The three assistant messages that open the first conversation."""

from __future__ import annotations

_WELCOME: dict[str, tuple[str, str, str]] = {
    "en": (
        "Hey {name}, I'm Yoreni",
        "I can manage schedules for life, work, and study, help with research, or prepare any report",
        "We can start wherever you like",
    ),
    "zh-CN": (
        "嘿，{name}，我是 Yoreni",
        "我可以管理生活、工作和学习的日程，可以帮助你完成研究，或准备任意报告",
        "我们可以从你喜欢的任何地方开始",
    ),
    "zh-TW": (
        "嘿，{name}，我是 Yoreni",
        "我可以管理生活、工作和學習的行程，可以協助你完成研究，或準備任何報告",
        "我們可以從你喜歡的任何地方開始",
    ),
    "fr": (
        "Bonjour {name}, je suis Yoreni",
        "Je peux gérer vos emplois du temps personnels, professionnels et scolaires, vous aider dans vos recherches ou préparer n'importe quel rapport",
        "Nous pouvons commencer où vous voulez",
    ),
    "ja": (
        "こんにちは、{name}。Yoreniです",
        "生活・仕事・勉強の予定を管理し、調べものを手伝ったり、レポートを準備したりできます",
        "好きなところから始めましょう",
    ),
    "ko": (
        "안녕하세요, {name}. 저는 Yoreni예요",
        "생활, 업무, 공부 일정을 관리하고, 조사를 돕거나 원하는 보고서를 준비할 수 있어요",
        "좋아하는 곳부터 시작해요",
    ),
    "es": (
        "Hola, {name}, soy Yoreni",
        "Puedo organizar tus horarios personales, laborales y de estudio, ayudarte con investigaciones o preparar cualquier informe",
        "Podemos empezar por donde prefieras",
    ),
    "pt-BR": (
        "Olá, {name}, eu sou a Yoreni",
        "Posso organizar sua rotina pessoal, profissional e de estudos, ajudar com pesquisas ou preparar qualquer relatório",
        "Podemos começar por onde você quiser",
    ),
    "vi": (
        "Chào {name}, mình là Yoreni",
        "Mình có thể quản lý lịch sinh hoạt, công việc và học tập, hỗ trợ bạn nghiên cứu hoặc chuẩn bị bất kỳ báo cáo nào",
        "Chúng ta có thể bắt đầu từ bất cứ đâu bạn thích",
    ),
    "id": (
        "Hai {name}, saya Yoreni",
        "Saya bisa mengelola jadwal kehidupan, pekerjaan, dan belajar, membantu riset, atau menyiapkan laporan apa pun",
        "Kita bisa mulai dari mana saja yang kamu suka",
    ),
}


def welcome_messages(locale: str | None, name: str) -> list[str]:
    """Return the localized opening messages for *name*."""
    template = _WELCOME.get(locale or "", _WELCOME["en"])
    return [line.format(name=name) for line in template]
