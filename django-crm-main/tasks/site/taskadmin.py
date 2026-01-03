from urllib.parse import urlencode

from django.contrib import admin, messages
from django.contrib.contenttypes.models import ContentType
from django.core.handlers.wsgi import WSGIRequest
from django.db.models import Case, Q, Value, When
from django.db.models.functions import Coalesce, Least
from django.http import HttpResponseRedirect
from django.template.defaultfilters import linebreaks
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from common.utils.helpers import (
    CONTENT_COPY_ICON,
    CONTENT_COPY_LINK,
    COPY_STR,
    add_chat_context,
    annotate_chat,
    get_active_users,
    get_delta_date,
    get_today,
    set_toggle_tooltip,
)
from tasks.forms import TaskForm
from tasks.models import Task, TaskStage
from tasks.site.tasksbasemodeladmin import (
    TASK_NEXT_STEP,
    TasksBaseModelAdmin,
    notify_task_or_project_closed,
)
from tasks.utils.admfilters import ByProject


COMPLETED_TITLE = gettext_lazy("I completed my part of the task")
task_was_created_str = _("The task was created")
subtask_was_created_str = _("The subtask was created")
the_subtask_str = _("The subtask")


class TaskAdmin(TasksBaseModelAdmin):
    actions = ["export_selected"]
    empty_value_display = ""
    filter_horizontal = ("responsible", "subscribers")
    form = TaskForm

    list_display = (
        "coloured_name",
        "next_step",
        "coloured_next_step_date",
        "priority_field",
        "stage",
        "chat_link",
        "responsible_list",
        "act",
        "coloured_due_date",
        "created",
        "id",
        "person",
        "content_copy",
    )

    radio_fields = {"stage": admin.HORIZONTAL}
    raw_id_fields = ("project", "task")

    # ------------------ LIST / CHANGE ------------------

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        set_toggle_tooltip("task_step_date_sorting", request, extra_context)
        next_url = request.get_full_path()
        url = reverse("toggle_default_sorting")
        extra_context["toggle_sorting_url"] = f"{url}?model=Task&next_url={next_url}"
        return super().changelist_view(request, extra_context)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        url = self.get_url_if_no_object(request, object_id)
        if url:
            return HttpResponseRedirect(url)

        extra_context = extra_context or {}
        extra_context["subtask_num"] = Task.objects.filter(task_id=object_id).count()

        task = Task.objects.get(id=object_id)
        stage = getattr(task, "stage", None)

        extra_context["show_completed"] = not any(
            (
                extra_context["subtask_num"],
                task.responsible.count() > 1,
                stage and stage.done,
            )
        )

        if not extra_context["show_completed"]:
            if request.user in task.responsible.all():
                extra_context["add_subtask_url"] = self.get_add_subtask_url(object_id)

        content_type = ContentType.objects.get_for_model(Task)
        add_chat_context(request, extra_context, object_id, content_type)
        self.add_remainder_context(request, extra_context, object_id, content_type)

        extra_context["content_copy_link"] = mark_safe(
            CONTENT_COPY_LINK.format(
                reverse("site:tasks_task_add") + f"?copy_task={object_id}",
                COPY_STR,
                CONTENT_COPY_ICON,
            )
        )

        return super().change_view(request, object_id, form_url, extra_context)

    # ------------------ FORM FIELDS ------------------

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "task":
            parent_task_id = request.GET.get("parent_task_id")
            if parent_task_id:
                kwargs["initial"] = parent_task_id
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name == "responsible":
            users = get_active_users()
            responsible = users

            if not any(
                (
                    request.user.is_chief,
                    request.user.is_superuser,
                    request.user.is_task_operator,
                )
            ):
                responsible = users.filter(id=request.user.id)
                kwargs["initial"] = responsible

            kwargs["queryset"] = responsible.order_by("username")

        return super().formfield_for_manytomany(db_field, request, **kwargs)

    # ------------------ INITIAL DATA ------------------

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        initial["next_step"] = TASK_NEXT_STEP
        initial["next_step_date"] = get_delta_date(1)
        initial["stage"] = TaskStage.objects.filter(default=True).first()
        return initial

    # ------------------ QUERYSET ------------------

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = qs.annotate(
            step_date=Least(
                Coalesce("task__next_step_date", "next_step_date"),
                "next_step_date",
            ),
            parent_id=Coalesce("task_id", "id"),
        )
        return qs.order_by("parent_id", "step_date", "id")

    def get_list_filter(self, request, obj=None):
        filters = super().get_list_filter(request, obj)
        filters.append(ByProject)
        return filters

    # ------------------ SAVE LOGIC ------------------

    def save_model(self, request, obj, form, change):
        stage = getattr(obj, "stage", None)

        if not stage:
            obj.stage = TaskStage.objects.filter(default=True).first()
            stage = obj.stage

        if "_completed" in request.POST:
            obj.stage = TaskStage.objects.filter(done=True).first()
            obj.closing_date = get_today()

        if stage and not stage.active:
            obj.closing_date = get_today()

        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        obj = form.instance
        main_task = obj.task

        if main_task:
            main_stage = getattr(main_task, "stage", None)
            obj_stage = getattr(obj, "stage", None)

            if main_stage and obj_stage:
                if main_stage.default and obj_stage.in_progress:
                    main_task.stage = obj_stage
                    main_task.save()

            if not obj.active:
                obj.copy_files_to_maintask()
                obj.check_and_deacte_main_task()
                if main_task and not main_task.active:
                    notify_task_or_project_closed(request, main_task)

    # ------------------ DISPLAY HELPERS ------------------

    @admin.display(description="")
    def content_copy(self, obj):
        url = reverse("site:tasks_task_add") + f"?copy_task={obj.id}"
        return mark_safe(CONTENT_COPY_LINK.format(url, COPY_STR, CONTENT_COPY_ICON))

    @admin.display(ordering="name")
    def coloured_name(self, obj):
        if not obj.task:
            return mark_safe(
                f'<span style="color:var(--green-fg)">{obj.name}</span>'
            )
        return mark_safe(f"<span style='margin-left:20px'>{obj.name}</span>")

    @staticmethod
    def get_add_subtask_url(object_id):
        params = {
            "parent_task_id": object_id,
            "next_url": reverse("site:tasks_task_changelist"),
        }
        return reverse("site:tasks_task_add") + "?" + urlencode(params)
