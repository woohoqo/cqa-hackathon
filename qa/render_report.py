"""Render compare_report JSON into a readable Markdown report without raw transcripts."""
import argparse
import json
from pathlib import Path


def percent(value):
    return 'не рассчитано' if value is None else f'{value:.2%}'


def number(value):
    return 'неизвестно' if value is None else f'{value:g}'


def render(report):
    ops=report['overall']['operations']
    primary=report['overall']['primary_first_attempt']
    lines=['# Отчёт сравнения CQA','',
           f"Источник меток: `{report['reference_source']}`; режим: `{report['mode']}`; часть: `{report['split']}`.",
           f"Чек-лист: `{report['checklist_hash']}`. Сравнение: `{report['comparison_kind']}`.",'',
           f"Запланировано: {ops['planned']}; начато: {ops['attempted']}; записей попыток: {ops['attempts']}; пригодных меток: {primary['labelled']}."]
    if not ops['attempts']:
        lines+=['','**Запусков нет: это отчёт готовности измерения, а не измеренное качество AI.**']
    if not primary['labelled']:
        lines+=['','**Точность не установлена: пригодных эталонных меток нет.**']
    lines+=['',f"Единица попытки: `{report.get('attempt_unit','recorded_attempt')}`. Внутреннее число вызовов провайдера неизвестно.",
            'Первичная оценка — попытка №1; апелляции и финальные исправления человека не используются.','',
            '| Критерий | Совпало / сравнимо | Совпадение по парам | Верно / все размеченные | Ложный N/A | Пропущенный N/A |',
            '|---|---:|---:|---:|---:|---:|']
    for cid,m in primary['per_criterion'].items():
        lines.append(f"| {cid} | {m['matches']} / {m['comparable']} | {percent(m['conditional_agreement'])} | {percent(m['end_to_end_correct'])} | {m['na']['false_na']} | {m['na']['missed_na']} |")
    t=primary['total_score'];c=primary['critical'];s=primary['stored_original_total_score']
    lines+=['',f"MAE итога, пересчитанного QA по исходным ответам: {number(t['mae'])}; пары / все размеченные: {t['pairs']} / {t['labelled']}.",
            f"MAE сохранённого первоначального original_score.total: {number(s['mae'])}; пары / все размеченные: {s['pairs']} / {s['labelled']}; покрытие: {percent(s['coverage_on_labelled'])}.",
            f"Сохранённый итог отсутствует у {s['missing_count']}, невалиден у {s['invalid_count']} сравнимых ответов; необработанных размеченных: {s['unprocessed_labelled_count']}. Пропуски не заменяются нулём.",
            f"Расхождения сохранённого и QA-пересчитанного итога по всем попыткам: {len(ops['reported_total_mismatch_attempt_ids'])} / {ops['reported_total_checked_successes']}; ID: {json.dumps(ops['reported_total_mismatch_attempt_ids'],ensure_ascii=False)}.",
            f"Грубые ошибки: TP={c['tp']}, FP={c['fp']}, FN={c['fn']}, TN={c['tn']}; необработанных положительных примеров: {len(c['unprocessed_reference_positive_ids'])}.",
            f"Запрещённый N/A по всем попыткам: `{json.dumps(ops['forbidden_na_by_criterion'],ensure_ascii=False)}`.",'',
            '| Язык | План | Метки | Сравнимо | MAE QA (пары/метки) | MAE сохранённого итога (пары/метки) | Успех первой / план |',
            '|---|---:|---:|---:|---:|---:|---:|']
    for language,section in report['by_language'].items():
        m=section['primary_first_attempt'];o=section['operations']
        t=m['total_score'];s=m['stored_original_total_score']
        lines.append(f"| {language} | {m['planned']} | {m['labelled']} | {m['comparable']} | {number(t['mae'])} ({t['pairs']}/{t['labelled']}) | {number(s['mae'])} ({s['pairs']}/{s['labelled']}) | {o['first_attempt_successes']} / {o['planned']} |")
    lines+=['',f"Не начато: {len(ops['not_started_ids'])}; неуспешных записей: {ops['non_success_attempts']}; записей с кодом ошибки/ограничения провайдера: {ops['api_error_attempts']}; невалидных успешных ответов: {ops['invalid_success_attempts']}.",
            'API-счётчик классифицирует коды ошибок записей. При экспорте backend jobs локальная пауза квоты может вернуть тот же код без HTTP-вызова; число реальных вызовов требует связанного журнала провайдера.',
            f"После повторов успешно: {ops['eventual_successes']} / {ops['planned']} (отдельная вторичная метрика).",
            f"Стоимость всех записанных попыток, USD: {number(ops['cost']['total_usd'])}; известна у {ops['cost']['known_attempts']} / {ops['cost']['attempts']}.",
            f"Время записанных попыток: median={number(ops['latency']['median_ms'])} ms; p95={number(ops['latency']['p95_ms'])} ms. Очередь/backoff/UI сюда не входят.",'',
            'Подробные ID пропусков, ошибок, невалидных меток, исключённых режимов и результатов всех попыток сохранены в исходном JSON. Этот краткий вид их не заменяет.','']
    return '\n'.join(lines)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('input');p.add_argument('output')
    a=p.parse_args();r=json.loads(Path(a.input).read_text(encoding='utf-8-sig'))
    Path(a.output).write_text(render(r),encoding='utf-8')


if __name__=='__main__':main()
