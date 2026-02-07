import os
import glob
import sys

# Default Target directory
DEFAULT_MIGRATIONS_DIR = "/home/syscall/LUND_IT/ArmiraCashflowDB/migrations"

# Replacements
REPLACEMENTS = {
    "import generic_app.generic_models.fields.PDF_field": "import lex.api.fields.PDF_field",
    "import generic_app.generic_models.fields.XLSX_field": "import lex.api.fields.XLSX_field",
    "import generic_app.generic_models.upload_model": "import lex.core.models.base\nimport lex.core.mixins.calculated", 
    "import django.utils.datetime_safe": "import django.utils.timezone",
    "import generic_app.generic_models.calculated_model": "import lex.core.mixins.calculated\nimport lex.core.models.base",

    # Field Defines
    "generic_app.generic_models.fields.PDF_field.PDFField": "lex.api.fields.PDF_field.PDFField",
    "generic_app.generic_models.fields.XLSX_field.XLSXField": "lex.api.fields.XLSX_field.XLSXField",
    "generic_app.generic_models.upload_model.IsCalculatedField": "models.BooleanField",
    "generic_app.generic_models.upload_model.CalculateField": "models.BooleanField",
    
    # Class Defines (Mixins & Base Models)
    "generic_app.generic_models.calculated_model.CalculatedModelMixin": "lex.core.mixins.calculated.CalculatedModelMixin",
    "generic_app.generic_models.upload_model.UploadModelMixin": "lex.core.models.base.UploadModelMixin",
    "generic_app.generic_models.upload_model.ConditionalUpdateMixin": "lex.core.models.base.ConditionalUpdateMixin",
    "generic_app.generic_models.LexModel": "lex.core.models.base.LexModel",
    "generic_app.generic_models.CalculationModel": "lex.core.models.calculation_model.CalculationModel",
    
    # Datetime
    "django.utils.datetime_safe.datetime.now": "django.utils.timezone.now",
}

def fix_migrations():
    if len(sys.argv) > 1:
        migrations_dir = sys.argv[1]
    else:
        migrations_dir = DEFAULT_MIGRATIONS_DIR
        
    print(f"Scanning directory: {migrations_dir}")
    
    # Find all python files in the migration directory
    migration_files = glob.glob(os.path.join(migrations_dir, "*.py"))
    
    for file_path in migration_files:
        if file_path.endswith("__init__.py"):
            continue
            
        print(f"Processing {file_path}...")
        with open(file_path, "r") as f:
            content = f.read()

        new_content = content
        changes_made = False
        
        for old, new in REPLACEMENTS.items():
            if old in new_content:
                new_content = new_content.replace(old, new)
                print(f"  [FIXED] {old} -> {new}")
                changes_made = True
            # No "else" print to reduce noise for files that don't need changes

        if changes_made:
            print(f"Writing fixed content to {file_path}...")
            with open(file_path, "w") as f:
                f.write(new_content)
        else:
            print(f"No changes needed for {file_path}.")
    
    print("All done.")

if __name__ == "__main__":
    fix_migrations()
