"""Initial configuration transcribed from Chat Quality Checklist, pages 1–3.

Only the configuration knows official IDs; evaluator/calculator remain data-driven.
"""
from .contracts import ChecklistCreate, Criterion, ScaleStep, Section


def official_checklist() -> ChecklistCreate:
    return ChecklistCreate(
        key="freedom-cqa", name="Чек-лист качества чатов Freedom Broker",
        description=(
            "Оценивается оператор. Реплики bot/system служат контекстом. Один чек-лист для RU, KZ и EN. "
            "Порог первой реакции в 3 минуты, теги и вложения в оценку не входят. "
            "По каждому критерию нужны короткое обоснование и ссылка на сообщение. "
            "Для пропущенного действия укажите место, где оно ожидалось; для N/A объясните выполненное условие. "
            "Консультация и ведение диалога оцениваются независимо: ясный ответ на один из двух важных вопросов "
            "может получить высокий балл за ведение и частичный за консультацию."
        ),
        sections=[Section(id="start", title="НАЧАЛО И ПОНИМАНИЕ ЗАПРОСА"),
                  Section(id="solution", title="БЕЗОПАСНОСТЬ И РЕШЕНИЕ"),
                  Section(id="dialogue", title="ВЕДЕНИЕ И ЗАВЕРШЕНИЕ ДИАЛОГА"),
                  Section(id="violations", title="ГРУБЫЕ НАРУШЕНИЯ")],
        criteria=[
            Criterion(id="1", section_id="start", title="Установление контакта", weight=10, answer_type="boolean",
                      description="Да — оператор поздоровался на языке клиента и представился в первом ответе."),
            Criterion(id="2", section_id="start", title="Выявление потребности", weight=15, answer_type="boolean",
                      description="Да — оператор учёл известные детали и уточнил недостающее до ответа.",
                      allow_na=True, na_condition="данных для ответа хватало с самого начала"),
            Criterion(id="3", section_id="solution", title="Идентификация", weight=20, answer_type="boolean",
                      description=("Да — проверка личности подтверждена до раскрытия данных или защищённого действия. "
                                   "Общий вопрос о тарифах не требует идентификации. Если проверка личности требовалась "
                                   "перед защищённым действием и не подтверждена в чате, ставьте Нет."),
                      allow_na=True, na_condition="запрос не требовал идентификации"),
            Criterion(id="4", section_id="solution", title="Корректная и полная консультация", weight=20,
                      answer_type="scale", description="Оцените ответ или следующий шаг по шкале.",
                      allow_na=True, na_condition="чат закончился до того, как клиент сформулировал запрос",
                      scale=[ScaleStep(value=value, label=str(value), description=description) for value, description in [
                          (0, "Ответа по существу нет или ключевая рекомендация ошибочна."),
                          (5, "Есть общая подсказка, но клиенту всё ещё непонятно, как действовать."),
                          (10, "Часть запроса закрыта, существенный вопрос остался открытым."),
                          (15, "Основной вопрос решён, пропущена второстепенная деталь."),
                          (20, "Все существенные вопросы закрыты или дан корректный следующий шаг.")]]),
            Criterion(id="5", section_id="dialogue", title="Эмпатия и клиентоориентированность", weight=15,
                      answer_type="boolean", description="Да — оператор вежлив и, если клиент выразил недовольство или описал неудобство, отреагировал на конкретную проблему."),
            Criterion(id="6", section_id="dialogue", title="Управление диалогом, скорость и грамотность", weight=15,
                      answer_type="scale", description="Оцените ясность, порядок ответов и предупреждение об ожидании по шкале.",
                      scale=[ScaleStep(value=value, label=str(value), description=description) for value, description in [
                          (0, "Из-за бессвязных или противоречивых реплик ход диалога непонятен."),
                          (4, "Клиенту приходится переспрашивать из-за формулировок или порядка ответов."),
                          (7, "Один заметный сбой, но клиент может понять ответ и продолжить."),
                          (10, "Ответы ясные и связные. Если было явное ожидание, оператор предупредил.")]]),
            Criterion(id="7", section_id="dialogue", title="Завершение", weight=5, answer_type="boolean",
                      description="Да — оператор подвёл итог или предложил дальнейшую помощь и попрощался.",
                      allow_na=True, na_condition="чат оборвался до возможности его завершить"),
            Criterion(id="8", section_id="violations", title="Грубые ошибки", weight=0, answer_type="flag",
                      description=("Да — выявлено оскорбление клиента, предложение платной услуги в обход компании или "
                                   "раскрытие конфиденциальной информации. Нет — ни одно из трёх не выявлено. "
                                   "Обход компании: оператор предлагает перевести деньги ему лично для инвестирования "
                                   "или просит личный номер клиента, чтобы оказать платную консультацию в частном порядке. "
                                   "Достаточно самого предложения; согласие клиента или перевод денег не требуются.")),
        ],
    )
