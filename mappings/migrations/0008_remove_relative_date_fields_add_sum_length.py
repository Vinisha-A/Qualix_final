from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mappings', '0007_mapping_date_operator_mapping_date_relative_operator_and_more'),
    ]

    operations = [
        # Remove relative date fields from Mapping
        migrations.RemoveField(
            model_name='mapping',
            name='date_value_type',
        ),
        migrations.RemoveField(
            model_name='mapping',
            name='date_relative_operator',
        ),
        migrations.RemoveField(
            model_name='mapping',
            name='date_relative_value',
        ),
        migrations.RemoveField(
            model_name='mapping',
            name='source_date_value_type',
        ),
        migrations.RemoveField(
            model_name='mapping',
            name='source_date_relative_operator',
        ),
        migrations.RemoveField(
            model_name='mapping',
            name='source_date_relative_value',
        ),
        migrations.RemoveField(
            model_name='mapping',
            name='target_date_value_type',
        ),
        migrations.RemoveField(
            model_name='mapping',
            name='target_date_relative_operator',
        ),
        migrations.RemoveField(
            model_name='mapping',
            name='target_date_relative_value',
        ),
        # Update ValidationRule operation choices to add sum_length
        migrations.AlterField(
            model_name='validationrule',
            name='operation',
            field=models.CharField(
                choices=[
                    ('count', 'Count'),
                    ('min', 'Minimum'),
                    ('max', 'Maximum'),
                    ('sum', 'Sum'),
                    ('distinct_count', 'Distinct Count'),
                    ('null_check', 'Null Check'),
                    ('duplicate_check', 'Duplicate Check'),
                    ('data_type_check', 'Data Type Check'),
                    ('row_count', 'Row Count Match'),
                    ('avg', 'Average'),
                    ('length_sum_check', 'Length Sum Check'),
                    ('sum_length', 'Sum Length'),
                    ('regex_check', 'Regex Check'),
                    ('unique_check', 'Unique Check'),
                    ('range_check', 'Range Check'),
                    ('min_date', 'Min Date'),
                    ('max_date', 'Max Date'),
                ],
                max_length=30,
            ),
        ),
    ]
