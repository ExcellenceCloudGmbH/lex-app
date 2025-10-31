"""
Django management command to check which models have or will have history tracking.
"""

from django.core.management.base import BaseCommand
from django.apps import apps
from django.db import models
from lex.lex_app.simple_history_config import should_track_model, get_model_exclusion_reason


class Command(BaseCommand):
    help = 'Check which models have or will have history tracking enabled'

    def add_arguments(self, parser):
        parser.add_argument(
            '--app',
            type=str,
            help='Check models only from specific app',
        )
        parser.add_argument(
            '--show-excluded',
            action='store_true',
            help='Show models that are excluded from history tracking',
        )
        parser.add_argument(
            '--show-tracked',
            action='store_true',
            help='Show models that have history tracking',
        )
        parser.add_argument(
            '--show-all',
            action='store_true',
            help='Show all models (tracked and excluded)',
        )

    def handle(self, *args, **options):
        app_label = options.get('app')
        show_excluded = options.get('show_excluded', False)
        show_tracked = options.get('show_tracked', False)
        show_all = options.get('show_all', False)
        
        # If no specific flags are set, show all by default
        if not (show_excluded or show_tracked):
            show_all = True

        tracked_models = []
        excluded_models = []
        already_tracked_models = []

        # Get all models
        if app_label:
            try:
                app_config = apps.get_app_config(app_label)
                models_to_check = app_config.get_models()
            except LookupError:
                self.stdout.write(
                    self.style.ERROR(f'App "{app_label}" not found.')
                )
                return
        else:
            models_to_check = apps.get_models()

        # Categorize models
        for model in models_to_check:
            if hasattr(model, 'history'):
                already_tracked_models.append(model)
            elif should_track_model(model):
                tracked_models.append(model)
            else:
                excluded_models.append(model)

        # Display results
        self.stdout.write(
            self.style.SUCCESS(f'\n=== History Tracking Status ===\n')
        )

        if show_all or show_tracked:
            self.stdout.write(
                self.style.SUCCESS(f'✓ Models WITH history tracking ({len(already_tracked_models)}):')
            )
            for model in sorted(already_tracked_models, key=lambda m: f"{m._meta.app_label}.{m.__name__}"):
                self.stdout.write(f'  • {model._meta.app_label}.{model.__name__}')
            
            self.stdout.write(
                self.style.WARNING(f'\n→ Models that WILL GET history tracking ({len(tracked_models)}):')
            )
            for model in sorted(tracked_models, key=lambda m: f"{m._meta.app_label}.{m.__name__}"):
                self.stdout.write(f'  • {model._meta.app_label}.{model.__name__}')

        if show_all or show_excluded:
            self.stdout.write(
                self.style.ERROR(f'\n⊘ Models EXCLUDED from history tracking ({len(excluded_models)}):')
            )
            for model in sorted(excluded_models, key=lambda m: f"{m._meta.app_label}.{m.__name__}"):
                reason = get_model_exclusion_reason(model)
                self.stdout.write(f'  • {model._meta.app_label}.{model.__name__} - {reason}')

        # Summary
        total_models = len(tracked_models) + len(excluded_models) + len(already_tracked_models)
        self.stdout.write(
            self.style.SUCCESS(f'\n=== Summary ===')
        )
        self.stdout.write(f'Total models: {total_models}')
        self.stdout.write(f'Already tracked: {len(already_tracked_models)}')
        self.stdout.write(f'Will be tracked: {len(tracked_models)}')
        self.stdout.write(f'Excluded: {len(excluded_models)}')
        
        if tracked_models:
            self.stdout.write(
                self.style.WARNING(f'\nTo apply history tracking to new models, run: python manage.py makemigrations')
            )