import os
from django import forms
from django.conf import settings
from django.forms import ModelForm, Textarea, ValidationError
from django.utils.translation import gettext_lazy as _

from common.utils.helpers import get_today
from tasks.models import Memo, Project, Task

ONE_RESPONSIBLE_MSG = "This is a sub-task, so specify only one responsible."


class TaskBaseForm(ModelForm):
    class Meta:
        fields = '__all__'
        widgets = {
            'name': Textarea(attrs={'cols': 80, 'rows': 2}),
            'next_step': Textarea(attrs={'cols': 80, 'rows': 2}),
            'description': Textarea(attrs={'cols': 80, 'rows': 7}),
            'note': Textarea(attrs={'cols': 80, 'rows': 3}),
            'task': forms.HiddenInput(),
            'token': forms.HiddenInput(),
        }

    def clean_name(self):
        name = self.cleaned_data.get('name')
        if not name or name == settings.NO_NAME_STR:
            raise ValidationError(_("Please specify a name"), code='invalid')
        return name

    def clean_responsible(self):
        responsible = self.cleaned_data.get('responsible')
        if not responsible:
            raise ValidationError(_("Please specify a responsible"), code='invalid')
        return responsible

    def clean(self):
        cleaned_data = super().clean()

        # Validate next step date
        clean_next_step_date(self)

        # ✅ SAFE due_date validation (FIXED)
        due_date = cleaned_data.get('due_date')
        if 'due_date' in self.changed_data and due_date:
            if due_date < get_today():
                raise ValidationError({
                    'due_date': _('Date should not be in the past.')
                }, code='invalid')

        # Sub-task must have only one responsible
        if (
            'responsible' in self.changed_data
            and cleaned_data.get('task')
            and cleaned_data.get('responsible')
        ):
            if len(cleaned_data.get('responsible')) > 1:
                raise ValidationError({
                    'responsible': _(ONE_RESPONSIBLE_MSG)
                }, code='invalid')

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit)

        parent_obj = None
        if getattr(self, 'parent_memo_id', None):
            parent_obj = Memo.objects.get(id=self.parent_memo_id)
        elif getattr(self, 'parent_task_id', None):
            parent_obj = Task.objects.get(id=self.parent_task_id)
        elif getattr(self, 'parent_project_id', None):
            parent_obj = Project.objects.get(id=self.parent_project_id)

        if parent_obj:
            files = parent_obj.files.all()
            if files:
                instance.attach_files = [
                    f for f in files
                    if self.cleaned_data.get(
                        os.path.basename(f.file.path), False
                    )
                ]

        return instance


class TaskForm(TaskBaseForm):
    class Meta(TaskBaseForm.Meta):
        model = Task

    class Media:
        css = {'all': ('/static/common/css/task_module.css',)}


class ProjectForm(TaskBaseForm):
    class Meta(TaskBaseForm.Meta):
        model = Project


class MemoForm(ModelForm):
    class Meta:
        model = Memo
        fields = '__all__'
        widgets = {
            'name': Textarea(attrs={'cols': 80, 'rows': 2}),
            'description': Textarea(attrs={'cols': 100, 'rows': 8}),
            'note': Textarea(attrs={'cols': 100, 'rows': 3}),
        }

    class Media:
        css = {'all': ('/static/common/css/memo_module.css',)}


def clean_next_step_date(form) -> None:
    remind_me = form.cleaned_data.get('remind_me')
    if (
        'next_step_date' in form.changed_data
        or ('remind_me' in form.changed_data and remind_me)
    ):
        next_step_date = form.cleaned_data.get('next_step_date')
        if next_step_date and next_step_date < get_today():
            raise ValidationError({
                'next_step_date': _('Date should not be in the past.')
            }, code='invalid')
