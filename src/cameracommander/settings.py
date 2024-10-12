import yaml

def load_settings(settings_file):
    with open(settings_file, 'r') as f:
        settings = yaml.safe_load(f)
    return settings

def save_settings(settings_dict, settings_file):
    with open(settings_file, 'w') as f:
        yaml.safe_dump(settings_dict, f)

