import os
import xml.etree.ElementTree as ET

file_lookup = {}
for root_dir, dirs, files in os.walk('.'):
    for file in files:
        file_lookup[file.lower()] = os.path.join(root_dir, file)

tree = ET.parse('rekordbox.xml')
root = tree.getroot()

count_fixed = 0
for track in root.iter('TRACK'):
    location = track.attrib.get('Location')
    if not location:
        continue

    # Decode original path
    original_path = location.replace('file://', '').replace('%20', ' ')
    
    # Only fix if file doesn't exist
    if not os.path.exists(original_path):
        filename = os.path.basename(original_path).lower()
        new_path = file_lookup.get(filename)
        if new_path:
            new_path_url = 'file://' + new_path.replace(' ', '%20').replace('&', '%26')
            track.set('Location', new_path_url)
            count_fixed += 1

tree.write('rekordbox_fixed.xml')
print(f'Fixed {count_fixed} paths.')
