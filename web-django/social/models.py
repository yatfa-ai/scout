"""Models for the existing `social` database.

Generated with `python manage.py inspectdb` against a database built from
`schema.sql` (the schema source of truth) and then hand-cleaned: explicit
`db_column` on the foreign keys, choices and defaults mirroring the CHECK
constraints and DEFAULTs in `schema.sql`, Russian verbose names for the admin,
and `__str__` for readable rows.

Every model is `managed = False`. Django owns no DDL here: `schema.sql` plus
`soc migrate` remain the only way the schema changes, so `manage.py migrate`
can never alter or drop anything in `social`.
"""

from django.db import models

# Mirrors of the CHECK constraints in schema.sql. Nothing in Django enforces
# the database constraint; these exist so the admin offers exactly the values
# the database accepts.
NETWORK_CHOICES = [
    ('linkedin', 'LinkedIn'),
    ('x', 'X'),
    ('reddit', 'Reddit'),
]

DRAFT_KIND_CHOICES = [
    ('post', 'пост'),
    ('comment', 'комментарий'),
    ('reply', 'ответ'),
    ('thread', 'тред'),
    ('connect', 'запрос в контакты'),
    ('dm', 'личное сообщение'),
]

DRAFT_STATUS_CHOICES = [
    ('draft', 'черновик'),
    ('waiting', 'ждёт решения'),
    ('approved', 'одобрено'),
    ('sent', 'отправлено'),
    ('skipped', 'отклонено'),
]

INTERACTION_DIRECTION_CHOICES = [
    ('out', 'от нас'),
    ('in', 'нам'),
]

LEAD_STAGE_CHOICES = [
    ('candidate', 'кандидат'),
    ('commented', 'прокомментировали'),
    ('connected', 'в контактах'),
    ('talking', 'общаемся'),
    ('client', 'клиент'),
    ('dead', 'не сложилось'),
]


class People(models.Model):
    id = models.TextField(primary_key=True)
    name = models.TextField(verbose_name='имя')
    network = models.TextField(choices=NETWORK_CHOICES, verbose_name='сеть')
    profile = models.TextField(blank=True, null=True, verbose_name='профиль')
    headline = models.TextField(blank=True, null=True, verbose_name='заголовок')
    followers = models.TextField(blank=True, null=True, verbose_name='подписчики')
    segment = models.TextField(blank=True, null=True, verbose_name='сегмент')
    relevance = models.TextField(blank=True, null=True, verbose_name='релевантность')
    first_seen = models.DateField(blank=True, null=True, verbose_name='впервые замечен')
    status = models.TextField(default='наблюдаем', verbose_name='статус')
    created_at = models.DateTimeField(verbose_name='создан')

    class Meta:
        managed = False
        db_table = 'people'
        verbose_name = 'человек'
        verbose_name_plural = 'люди'

    def __str__(self):
        return f'{self.name} ({self.network})'


class Drafts(models.Model):
    id = models.BigAutoField(primary_key=True)
    network = models.TextField(choices=NETWORK_CHOICES, verbose_name='сеть')
    kind = models.TextField(choices=DRAFT_KIND_CHOICES, verbose_name='тип')
    status = models.TextField(
        choices=DRAFT_STATUS_CHOICES, default='waiting', verbose_name='статус'
    )
    target_person = models.ForeignKey(
        People,
        models.DO_NOTHING,
        db_column='target_person',
        blank=True,
        null=True,
        verbose_name='адресат',
    )
    target_url = models.TextField(blank=True, null=True, verbose_name='куда публиковать')
    target_note = models.TextField(blank=True, null=True, verbose_name='о чём и для кого')
    body = models.TextField(verbose_name='текст')
    facts = models.TextField(blank=True, null=True, verbose_name='факты и источники')
    question = models.TextField(blank=True, null=True, verbose_name='вопрос approver-у')
    created_at = models.DateTimeField(verbose_name='создан')
    approved_at = models.DateTimeField(blank=True, null=True, verbose_name='одобрен')
    sent_at = models.DateTimeField(blank=True, null=True, verbose_name='отправлен')
    sent_url = models.TextField(blank=True, null=True, verbose_name='ссылка на публикацию')
    skip_reason = models.TextField(blank=True, null=True, verbose_name='причина отказа')

    class Meta:
        managed = False
        db_table = 'drafts'
        verbose_name = 'черновик'
        verbose_name_plural = 'черновики'

    def __str__(self):
        return f'#{self.id} {self.network}/{self.kind} [{self.status}]'


class Interactions(models.Model):
    id = models.TextField(primary_key=True)
    # Kept as text: timestamps here are sometimes approximate (a leading "~").
    ts = models.TextField(verbose_name='когда')
    network = models.TextField(choices=NETWORK_CHOICES, verbose_name='сеть')
    direction = models.TextField(
        choices=INTERACTION_DIRECTION_CHOICES, verbose_name='направление'
    )
    kind = models.TextField(verbose_name='тип')
    # No foreign key in schema.sql: this column is free-form in the database.
    person = models.TextField(blank=True, null=True, verbose_name='человек')
    post_url = models.TextField(blank=True, null=True, verbose_name='ссылка')
    parent_url = models.TextField(blank=True, null=True, verbose_name='родительская ссылка')
    in_reply_to = models.ForeignKey(
        'self',
        models.DO_NOTHING,
        db_column='in_reply_to',
        blank=True,
        null=True,
        verbose_name='в ответ на',
    )
    text = models.TextField(blank=True, null=True, verbose_name='текст')
    draft = models.ForeignKey(
        Drafts,
        models.DO_NOTHING,
        db_column='draft_id',
        blank=True,
        null=True,
        verbose_name='черновик',
    )
    # Legacy reference to a draft file, kept for history only.
    draft_ref = models.TextField(blank=True, null=True, verbose_name='старая ссылка на черновик')
    stats = models.JSONField(blank=True, null=True, verbose_name='статистика')
    note = models.TextField(blank=True, null=True, verbose_name='заметка')

    class Meta:
        managed = False
        db_table = 'interactions'
        verbose_name = 'взаимодействие'
        verbose_name_plural = 'взаимодействия'

    def __str__(self):
        return f'{self.ts} {self.network} {self.direction}/{self.kind}'


class Questions(models.Model):
    id = models.TextField(primary_key=True)
    date = models.DateField(verbose_name='дата')
    # No CHECK on questions.network in schema.sql, so no choices here either.
    network = models.TextField(verbose_name='сеть')
    asker = models.ForeignKey(
        People,
        models.DO_NOTHING,
        db_column='asker',
        blank=True,
        null=True,
        verbose_name='спросил',
    )
    asker_role = models.TextField(blank=True, null=True, verbose_name='роль спросившего')
    to_us = models.BooleanField(default=False, verbose_name='спросили нас напрямую')
    question = models.TextField(verbose_name='вопрос')
    context = models.TextField(blank=True, null=True, verbose_name='контекст')
    source = models.ForeignKey(
        Interactions,
        models.DO_NOTHING,
        db_column='source',
        blank=True,
        null=True,
        verbose_name='источник',
    )
    url = models.TextField(blank=True, null=True, verbose_name='ссылка')
    topic = models.TextField(blank=True, null=True, verbose_name='тема')
    yatfa_answer = models.TextField(blank=True, null=True, verbose_name='наш ответ')
    status = models.TextField(blank=True, null=True, verbose_name='статус')

    class Meta:
        managed = False
        db_table = 'questions'
        verbose_name = 'вопрос'
        verbose_name_plural = 'вопросы'

    def __str__(self):
        return f'{self.date} {self.network}: {self.question[:60]}'


class Ledger(models.Model):
    id = models.BigAutoField(primary_key=True)
    # Kept as text, like interactions.ts.
    at = models.TextField(verbose_name='когда')
    # No CHECK on ledger.network in schema.sql, so no choices here either.
    network = models.TextField(verbose_name='сеть')
    topic = models.TextField(verbose_name='тема')
    url = models.TextField(blank=True, null=True, verbose_name='ссылка')
    outcome = models.TextField(blank=True, null=True, verbose_name='результат')

    class Meta:
        managed = False
        db_table = 'ledger'
        verbose_name = 'запись журнала'
        verbose_name_plural = 'журнал отправок'

    def __str__(self):
        return f'{self.at} {self.network}: {self.topic}'


class Leads(models.Model):
    id = models.BigAutoField(primary_key=True)
    # OneToOne, not ForeignKey: schema.sql carries a unique index
    # (leads_person_uniq) over leads(person), so one person has one lead row.
    person = models.OneToOneField(
        People, models.DO_NOTHING, db_column='person', verbose_name='человек'
    )
    stage = models.TextField(
        choices=LEAD_STAGE_CHOICES, default='candidate', verbose_name='этап'
    )
    source_url = models.TextField(blank=True, null=True, verbose_name='где нашли')
    pain = models.TextField(blank=True, null=True, verbose_name='боль, их словами')
    next_step = models.TextField(blank=True, null=True, verbose_name='следующий шаг')
    created_at = models.DateTimeField(verbose_name='создан')
    updated_at = models.DateTimeField(verbose_name='обновлён')

    class Meta:
        managed = False
        db_table = 'leads'
        verbose_name = 'лид'
        verbose_name_plural = 'лиды'

    def __str__(self):
        return f'{self.person_id} [{self.stage}]'
