from datetime import timedelta
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from common.utils.helpers import get_today, USER_MODEL
from tasks.models.taskbase import TaskBase


class Task(TaskBase):
    class Meta:
        verbose_name = _("Task")
        verbose_name_plural = _("Tasks")

    task = models.ForeignKey(
        "self",
        blank=True,
        null=True,
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)s_task_related",
        verbose_name=_("task"),
    )

    project = models.ForeignKey(
        "Project",
        blank=True,
        null=True,
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)s_project_related",
        verbose_name=_("project"),
    )

    # ✅ FIX 1: stage must be nullable
    stage = models.ForeignKey(
        "TaskStage",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        verbose_name=_("Stage"),
    )

    hide_main_task = models.BooleanField(
        default=False,
        verbose_name=_("Hide main task"),
        help_text=_("Hide the main task when this sub-task is closed."),
    )

    lead_time = models.DurationField(
        blank=True,
        default=timedelta(minutes=0),
        verbose_name=_("Lead time"),
        help_text=_("Task execution time in format - DD HH:MM:SS"),
    )

    def get_absolute_url(self):
        return reverse("site:tasks_task_change", args=(self.id,))

    def clean(self):
        """
        Prevent closing a task if it has active subtasks
        """
        self_id = getattr(self, "id", None)

        if self_id and not self.task and self.stage:
            if not self.stage.active:
                is_active_subtask = Task.objects.filter(
                    task__id=self_id,
                    stage__active=True
                ).exists()
                if is_active_subtask:
                    raise ValidationError({
                        "stage": _(
                            "The task cannot be closed because there is an active subtask."
                        )
                    })

        super().clean()

    # ✅ FIX 2: SAFE save() method
    def save(self, *args, **kwargs):
        stage = getattr(self, "stage", None)

        if stage:
            self.active = stage.active

        if not self.lead_time:
            self.lead_time = timedelta(minutes=0)

        super().save(*args, **kwargs)

    # -- Custom methods -- #

    def check_and_deacte_main_task(self) -> None:
        """
        Deactivate main task if all subtasks are completed
        """
        main_task = self.task
        if not main_task or not main_task.stage:
            return

        if main_task.stage.default or main_task.stage.in_progress:
            subtasks = Task.objects.filter(task=main_task)

            if not subtasks.filter(active=True).exists():
                done_subtasks = subtasks.filter(stage__done=True, active=False)

                done_responsible = USER_MODEL.objects.filter(
                    tasks_task_responsible_related__in=done_subtasks
                )

                responsible = main_task.responsible.all()

                if set(responsible) == set(done_responsible):
                    done_stage = main_task.stage.__class__.objects.filter(done=True).first()

                    if done_stage:
                        today = get_today()
                        main_task.stage = done_stage
                        main_task.add_to_workflow(
                            _("The main task is closed automatically.")
                        )
                        main_task.next_step = _("Done")
                        main_task.next_step_date = today
                        main_task.closing_date = today
                        main_task.save()

    def copy_files_to_maintask(self) -> None:
        """
        Copy unique files from a subtask to the main task
        """
        if not self.task:
            return

        maintask_files = [f.file for f in self.task.files.all()]
        uniq_files = self.files.exclude(file__in=maintask_files)

        if uniq_files:
            file_model = apps.get_model("common", "TheFile")
            for f in uniq_files:
                file_model.objects.create(
                    file=f.file,
                    content_object=self.task
                )
