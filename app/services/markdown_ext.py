import re
import sqlite3
from markdown.extensions import Extension
from markdown.inlinepatterns import InlineProcessor
import xml.etree.ElementTree as ET

# Pattern for [[target|display]] or [[target]]
WIKI_LINK_RE = r'\[\[([^\]|#]+)(?:#([^\]|]+))?(?:\|([^\]]+))?\]\]'

class WikiLinkProcessor(InlineProcessor):
    def __init__(self, pattern, config):
        super().__init__(pattern)
        self.config = config

    def handleMatch(self, m, data):
        target = m.group(1).strip()
        anchor = m.group(2)
        display = m.group(3)
        
        project = self.config.get('project')
        db_path = self.config.get('db_path')
        
        entity_id = None
        real_name = None
        
        if target.startswith('ent_'):
            entity_id = target
        else:
            # Resolve name to ID
            try:
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT id, name FROM entities WHERE project=? AND (name=? OR aliases LIKE ?)",
                    (project, target, f'%"{target}"%')
                ).fetchone()
                if row:
                    entity_id = row['id']
                    real_name = row['name']
                conn.close()
            except:
                pass

        if entity_id and not real_name:
            # Resolve ID to name
            try:
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                row = conn.execute("SELECT name FROM entities WHERE id=?", (entity_id,)).fetchone()
                if row:
                    real_name = row['name']
                conn.close()
            except:
                pass

        final_display = real_name or display or target
        
        a = ET.Element('a')
        if entity_id:
            a.set('href', f'/projects/{project}/entities/{entity_id}')
            a.set('class', 'wiki-link entity-link')
        else:
            a.set('href', '#')
            a.set('class', 'wiki-link unbound-link')
            
        a.text = final_display
        return a, m.start(0), m.end(0)

class WikiLinkExtension(Extension):
    def __init__(self, **kwargs):
        self.config = {
            'project': ['', 'Project slug'],
            'db_path': ['', 'Path to novelhub.db']
        }
        super().__init__(**kwargs)

    def extendMarkdown(self, md):
        proc = WikiLinkProcessor(WIKI_LINK_RE, self.getConfigs())
        md.inlinePatterns.register(proc, 'wiki_link', 175)

def makeExtension(**kwargs):
    return WikiLinkExtension(**kwargs)
