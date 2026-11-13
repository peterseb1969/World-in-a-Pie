"""Auto-generated from WIP OpenAPI specs and tools.yaml. Do not edit manually.

Regenerate with: python -m scripts.generate_schemas

The server appends TOOL_FIELD_DOCS[tool] to that tool's docstring and
replaces the parameter TOOL_PARAMS[tool] names with TOOL_SCHEMAS[ref].

Generated from:
  schemas/registry.json  version 0.4.0  sha256 42f5c7aa0a79ab7d
  schemas/def-store.json  version 0.2.0  sha256 98630ad9718fb202
  schemas/template-store.json  version 0.2.0  sha256 13f9ad3ba85f12ef
  schemas/document-store.json  version 0.2.0  sha256 5c1acf3b8c8be65e
  schemas/reporting-sync.json  version 0.1.0  sha256 b07e955adb46636c
"""


TOOL_SCHEMAS: dict[str, dict] = {'def-store#CreateTermRelationRequest': {'properties': {'source_term_id': {'type': 'string',
                                                                           'description': 'The '
                                                                                          'subject '
                                                                                          'term: '
                                                                                          'canonical '
                                                                                          'UUID, '
                                                                                          'fully '
                                                                                          'qualified '
                                                                                          "'ns:terminology:value', "
                                                                                          'or — '
                                                                                          'with '
                                                                                          'source_terminology '
                                                                                          'set — '
                                                                                          'the '
                                                                                          'OPAQUE '
                                                                                          'raw '
                                                                                          'term '
                                                                                          'value '
                                                                                          '(never '
                                                                                          'colon-parsed). '
                                                                                          'The '
                                                                                          'ambiguous '
                                                                                          '2-part '
                                                                                          "'TERMINOLOGY:VALUE' "
                                                                                          'shorthand '
                                                                                          'is '
                                                                                          'rejected.'},
                                                        'target_term_id': {'type': 'string',
                                                                           'description': 'The '
                                                                                          'object '
                                                                                          'term; '
                                                                                          'same '
                                                                                          'accepted '
                                                                                          'forms '
                                                                                          'as '
                                                                                          'source_term_id, '
                                                                                          'scoped '
                                                                                          'by '
                                                                                          'target_terminology.'},
                                                        'source_terminology': {'type': 'string',
                                                                               'description': 'Terminology '
                                                                                              'scoping '
                                                                                              'a '
                                                                                              'value-form '
                                                                                              'source_term_id. '
                                                                                              'When '
                                                                                              'set, '
                                                                                              'source_term_id '
                                                                                              'is '
                                                                                              'treated '
                                                                                              'as '
                                                                                              'the '
                                                                                              'raw '
                                                                                              'value '
                                                                                              '— '
                                                                                              'required '
                                                                                              'for '
                                                                                              'values '
                                                                                              'that '
                                                                                              'themselves '
                                                                                              'contain '
                                                                                              "':' "
                                                                                              '(OBO '
                                                                                              'ids '
                                                                                              'like '
                                                                                              'GO:0000278). '
                                                                                              'Per-item '
                                                                                              'because '
                                                                                              'a '
                                                                                              "relation's "
                                                                                              'two '
                                                                                              'endpoints '
                                                                                              'may '
                                                                                              'live '
                                                                                              'in '
                                                                                              'different '
                                                                                              'terminologies.'},
                                                        'target_terminology': {'type': 'string',
                                                                               'description': 'Terminology '
                                                                                              'scoping '
                                                                                              'a '
                                                                                              'value-form '
                                                                                              'target_term_id '
                                                                                              '(see '
                                                                                              'source_terminology).'},
                                                        'relation_type': {'type': 'string',
                                                                          'description': 'Relation '
                                                                                         'type '
                                                                                         'value '
                                                                                         '(e.g., '
                                                                                         "'is_a', "
                                                                                         "'part_of')"},
                                                        'metadata': {'additionalProperties': True,
                                                                     'type': 'object',
                                                                     'description': 'Provenance, '
                                                                                    'confidence, '
                                                                                    'OWL axioms'},
                                                        'created_by': {'type': 'string',
                                                                       'description': 'User or '
                                                                                      'system '
                                                                                      'creating '
                                                                                      'this '
                                                                                      'relation'}},
                                         'additionalProperties': False,
                                         'type': 'object',
                                         'required': ['source_term_id',
                                                      'target_term_id',
                                                      'relation_type'],
                                         'description': 'Request to create a typed relation '
                                                        'between two terms.'},
 'def-store#CreateTermRequest': {'properties': {'value': {'type': 'string',
                                                          'description': 'The value stored in '
                                                                         'documents (unique within '
                                                                         'terminology)'},
                                                'term_id': {'type': 'string',
                                                            'description': 'Pre-assigned term ID '
                                                                           '(for restore/migration '
                                                                           '— Registry uses as-is '
                                                                           'instead of '
                                                                           'generating)'},
                                                'names': {'items': {'properties': {'value': {'type': 'string',
                                                                                             'description': 'The '
                                                                                                            'name '
                                                                                                            'itself '
                                                                                                            '(e.g., '
                                                                                                            "'Avastin', "
                                                                                                            "'bevacizumab', "
                                                                                                            "'NSC-704865')"},
                                                                                   'type': {'type': 'string',
                                                                                            'description': 'Name '
                                                                                                           'type '
                                                                                                           'from '
                                                                                                           'the '
                                                                                                           '_NAME_TYPES '
                                                                                                           'terminology '
                                                                                                           '(e.g., '
                                                                                                           "'INN', "
                                                                                                           "'TRADE_GLOBAL', "
                                                                                                           "'CODE'). "
                                                                                                           'None '
                                                                                                           '= '
                                                                                                           'untyped '
                                                                                                           'synonym.'},
                                                                                   'language': {'type': 'string',
                                                                                                'description': 'Language '
                                                                                                               'code '
                                                                                                               '(ISO '
                                                                                                               '639-1) '
                                                                                                               'for '
                                                                                                               'language-specific '
                                                                                                               'names'},
                                                                                   'description': {'type': 'string',
                                                                                                   'description': 'Free-text '
                                                                                                                  'note '
                                                                                                                  'on '
                                                                                                                  'this '
                                                                                                                  'name '
                                                                                                                  '(e.g., '
                                                                                                                  'a '
                                                                                                                  'translated '
                                                                                                                  'description)'},
                                                                                   'source': {'type': 'string',
                                                                                              'description': 'Where '
                                                                                                             'this '
                                                                                                             'name '
                                                                                                             'came '
                                                                                                             'from '
                                                                                                             '(e.g., '
                                                                                                             "'RTiS', "
                                                                                                             "'OBO:hasExactSynonym')"},
                                                                                   'preferred': {'type': 'boolean',
                                                                                                 'description': 'Preferred '
                                                                                                                'name '
                                                                                                                'within '
                                                                                                                'its '
                                                                                                                '(type, '
                                                                                                                'language) '
                                                                                                                'group '
                                                                                                                '— '
                                                                                                                'at '
                                                                                                                'most '
                                                                                                                'one '
                                                                                                                'per '
                                                                                                                'group',
                                                                                                 'default': False}},
                                                                    'type': 'object',
                                                                    'required': ['value'],
                                                                    'description': 'One name of a '
                                                                                   'term — the '
                                                                                   'unified naming '
                                                                                   'model.\n'
                                                                                   '\n'
                                                                                   'A term is a '
                                                                                   'code (`value`) '
                                                                                   'plus a set of '
                                                                                   'names. Trade '
                                                                                   'names,\n'
                                                                                   'development '
                                                                                   'codes, '
                                                                                   'registry '
                                                                                   'numbers, '
                                                                                   'translated '
                                                                                   'labels and '
                                                                                   'plain\n'
                                                                                   'synonyms are '
                                                                                   'all names; '
                                                                                   'what '
                                                                                   'distinguishes '
                                                                                   'them is data '
                                                                                   'on the name,\n'
                                                                                   'not which '
                                                                                   'field they '
                                                                                   'live in. Every '
                                                                                   'name resolves '
                                                                                   'in validation\n'
                                                                                   'exactly like '
                                                                                   "the term's "
                                                                                   'value does.'},
                                                          'type': 'array',
                                                          'description': 'Alternative names that '
                                                                         'resolve to this term, '
                                                                         'each with optional type, '
                                                                         'language, source and '
                                                                         'preferred flag'},
                                                'aliases': {'items': {'type': 'string'},
                                                            'type': 'array',
                                                            'description': 'Legacy shape: plain '
                                                                           'alternative values. '
                                                                           'Normalized into '
                                                                           'untyped names on '
                                                                           'write.'},
                                                'label': {'type': 'string',
                                                          'description': 'Display label for UI. '
                                                                         'Defaults to value if not '
                                                                         'provided.'},
                                                'description': {'type': 'string',
                                                                'description': 'Detailed '
                                                                               'description'},
                                                'sort_order': {'type': 'integer',
                                                               'description': 'Sort order within '
                                                                              'terminology',
                                                               'default': 0},
                                                'parent_term_id': {'type': 'string',
                                                                   'description': 'Parent term for '
                                                                                  'hierarchical '
                                                                                  'terms: an '
                                                                                  'active term of '
                                                                                  'the same '
                                                                                  'terminology, by '
                                                                                  'id or (on this '
                                                                                  'door) by its '
                                                                                  'value'},
                                                'translations': {'items': {'properties': {'language': {'type': 'string',
                                                                                                       'description': 'Language '
                                                                                                                      'code '
                                                                                                                      '(ISO '
                                                                                                                      '639-1)'},
                                                                                          'label': {'type': 'string',
                                                                                                    'description': 'Translated '
                                                                                                                   'display '
                                                                                                                   'label'},
                                                                                          'description': {'type': 'string',
                                                                                                          'description': 'Translated '
                                                                                                                         'description'}},
                                                                           'type': 'object',
                                                                           'required': ['language',
                                                                                        'label'],
                                                                           'description': 'Legacy '
                                                                                          'translation '
                                                                                          'shape, '
                                                                                          'kept '
                                                                                          'for the '
                                                                                          'API '
                                                                                          'edge '
                                                                                          'only.\n'
                                                                                          '\n'
                                                                                          'Stored '
                                                                                          'terms '
                                                                                          'no '
                                                                                          'longer '
                                                                                          'carry a '
                                                                                          'translations '
                                                                                          'list — '
                                                                                          'a '
                                                                                          'translation '
                                                                                          'is a\n'
                                                                                          'TermName '
                                                                                          'with a '
                                                                                          'language. '
                                                                                          'Requests '
                                                                                          'may '
                                                                                          'still '
                                                                                          'submit '
                                                                                          'this '
                                                                                          'shape; '
                                                                                          'it is\n'
                                                                                          'normalized '
                                                                                          'into '
                                                                                          'names '
                                                                                          'on '
                                                                                          'write, '
                                                                                          'and '
                                                                                          'term '
                                                                                          'responses '
                                                                                          'derive '
                                                                                          'it back '
                                                                                          'for\n'
                                                                                          'callers '
                                                                                          'that '
                                                                                          'still '
                                                                                          'read '
                                                                                          'it.'},
                                                                 'type': 'array',
                                                                 'description': 'Legacy shape: '
                                                                                'per-language '
                                                                                'labels. '
                                                                                'Normalized into '
                                                                                'language-bearing '
                                                                                'names on write.'},
                                                'metadata': {'additionalProperties': True,
                                                             'type': 'object',
                                                             'description': 'Custom metadata'},
                                                'created_by': {'type': 'string',
                                                               'description': 'User or system '
                                                                              'creating this '
                                                                              'term'}},
                                 'additionalProperties': False,
                                 'type': 'object',
                                 'required': ['value'],
                                 'description': 'Request to create a new term.'},
 'def-store#CreateTerminologyRequest': {'properties': {'value': {'type': 'string',
                                                                 'description': 'Human-readable '
                                                                                'value (e.g., '
                                                                                "'DOC_STATUS')"},
                                                       'label': {'type': 'string',
                                                                 'description': 'Display label'},
                                                       'description': {'type': 'string',
                                                                       'description': 'Detailed '
                                                                                      'description'},
                                                       'terminology_id': {'type': 'string',
                                                                          'description': 'Pre-assigned '
                                                                                         'terminology '
                                                                                         'ID (for '
                                                                                         'restore/migration '
                                                                                         '— '
                                                                                         'Registry '
                                                                                         'uses '
                                                                                         'as-is '
                                                                                         'instead '
                                                                                         'of '
                                                                                         'generating)'},
                                                       'namespace': {'type': 'string',
                                                                     'description': 'Namespace for '
                                                                                    'the '
                                                                                    'terminology'},
                                                       'case_sensitive': {'type': 'boolean',
                                                                          'description': 'Whether '
                                                                                         'term '
                                                                                         'values '
                                                                                         'are '
                                                                                         'case-sensitive',
                                                                          'default': False},
                                                       'allow_multiple': {'type': 'boolean',
                                                                          'description': 'Whether '
                                                                                         'multiple '
                                                                                         'terms '
                                                                                         'can be '
                                                                                         'selected',
                                                                          'default': False},
                                                       'mutable': {'type': 'boolean',
                                                                   'description': 'Whether terms '
                                                                                  'can be '
                                                                                  'hard-deleted '
                                                                                  '(vs deprecated)',
                                                                   'default': False},
                                                       'unique_names': {'type': 'boolean',
                                                                        'description': 'Reject '
                                                                                       '(not just '
                                                                                       'warn '
                                                                                       'about) '
                                                                                       'term '
                                                                                       'writes '
                                                                                       'whose '
                                                                                       'names '
                                                                                       'collide '
                                                                                       'with '
                                                                                       'another '
                                                                                       "term's "
                                                                                       'value or '
                                                                                       'name',
                                                                        'default': False},
                                                       'metadata': {'properties': {'source': {'type': 'string',
                                                                                              'description': 'Source '
                                                                                                             'of '
                                                                                                             'the '
                                                                                                             'terminology '
                                                                                                             '(e.g., '
                                                                                                             "'ISO "
                                                                                                             "3166', "
                                                                                                             "'internal')"},
                                                                                   'source_url': {'type': 'string',
                                                                                                  'description': 'URL '
                                                                                                                 'to '
                                                                                                                 'the '
                                                                                                                 'source '
                                                                                                                 'specification'},
                                                                                   'version': {'type': 'string',
                                                                                               'description': 'Version '
                                                                                                              'of '
                                                                                                              'the '
                                                                                                              'terminology '
                                                                                                              '(e.g., '
                                                                                                              "'2024.1')"},
                                                                                   'language': {'type': 'string',
                                                                                                'description': 'Primary '
                                                                                                               'language '
                                                                                                               'code '
                                                                                                               '(ISO '
                                                                                                               '639-1)',
                                                                                                'default': 'en'},
                                                                                   'custom': {'additionalProperties': True,
                                                                                              'type': 'object',
                                                                                              'description': 'Custom '
                                                                                                             'metadata '
                                                                                                             'fields'}},
                                                                    'type': 'object',
                                                                    'description': 'Additional '
                                                                                   'metadata'},
                                                       'created_by': {'type': 'string',
                                                                      'description': 'Ignored: '
                                                                                     'created_by '
                                                                                     'is the '
                                                                                     'authenticated '
                                                                                     'caller. '
                                                                                     'Accepted for '
                                                                                     'compatibility'}},
                                        'additionalProperties': False,
                                        'type': 'object',
                                        'required': ['value', 'label', 'namespace'],
                                        'description': 'Request to create a new terminology.'},
 'document-store#DocumentCreateRequest': {'properties': {'template_id': {'type': 'string',
                                                                         'description': 'Template '
                                                                                        'ID to '
                                                                                        'validate '
                                                                                        'against'},
                                                         'template_version': {'type': 'integer',
                                                                              'description': 'Specific '
                                                                                             'template '
                                                                                             'version '
                                                                                             'to '
                                                                                             'validate '
                                                                                             'against '
                                                                                             '(default: '
                                                                                             'latest)'},
                                                         'document_id': {'type': 'string',
                                                                         'description': 'With '
                                                                                        'version: '
                                                                                        'the '
                                                                                        'pre-assigned '
                                                                                        'document '
                                                                                        'ID of a '
                                                                                        'restore '
                                                                                        'or '
                                                                                        'migration, '
                                                                                        'which the '
                                                                                        'Registry '
                                                                                        'uses '
                                                                                        'as-is. '
                                                                                        'Without '
                                                                                        'version: '
                                                                                        'the '
                                                                                        'document '
                                                                                        'this '
                                                                                        'write '
                                                                                        'updates — '
                                                                                        'it must '
                                                                                        'be the '
                                                                                        'one the '
                                                                                        'identity '
                                                                                        'resolves '
                                                                                        'to, or '
                                                                                        'the item '
                                                                                        'is '
                                                                                        'refused '
                                                                                        '(invalid_request, '
                                                                                        'details.rule '
                                                                                        'document_id_mismatch).'},
                                                         'version': {'type': 'integer',
                                                                     'description': 'Pre-assigned '
                                                                                    'version (for '
                                                                                    'restore/migration '
                                                                                    '— skips '
                                                                                    'Registry and '
                                                                                    'version '
                                                                                    'computation '
                                                                                    'when used '
                                                                                    'with '
                                                                                    'document_id)'},
                                                         'namespace': {'type': 'string',
                                                                       'description': 'Namespace '
                                                                                      'for the '
                                                                                      'document'},
                                                         'data': {'additionalProperties': True,
                                                                  'type': 'object',
                                                                  'description': 'Document '
                                                                                 'content'},
                                                         'created_by': {'type': 'string',
                                                                        'description': 'User or '
                                                                                       'system '
                                                                                       'creating '
                                                                                       'this '
                                                                                       'document'},
                                                         'metadata': {'additionalProperties': True,
                                                                      'type': 'object',
                                                                      'description': 'Custom '
                                                                                     'metadata'},
                                                         'synonyms': {'items': {'additionalProperties': True,
                                                                                'type': 'object'},
                                                                      'type': 'array',
                                                                      'description': 'Optional '
                                                                                     'synonym '
                                                                                     'composite '
                                                                                     'keys to '
                                                                                     'register for '
                                                                                     'this '
                                                                                     'document in '
                                                                                     'the '
                                                                                     'Registry'},
                                                         'on_synonym_conflict': {'type': 'string',
                                                                                 'enum': ['fail',
                                                                                          'warn'],
                                                                                 'description': 'How '
                                                                                                'to '
                                                                                                'handle '
                                                                                                'an '
                                                                                                'inline '
                                                                                                'synonym '
                                                                                                'that '
                                                                                                'the '
                                                                                                'Registry '
                                                                                                'refuses '
                                                                                                '(e.g. '
                                                                                                'already '
                                                                                                'owned '
                                                                                                'by '
                                                                                                'a '
                                                                                                'different '
                                                                                                'entry). '
                                                                                                "'fail' "
                                                                                                '(default): '
                                                                                                'the '
                                                                                                'whole '
                                                                                                'create '
                                                                                                'fails, '
                                                                                                'the '
                                                                                                'document '
                                                                                                'is '
                                                                                                'NOT '
                                                                                                'created, '
                                                                                                'and '
                                                                                                'any '
                                                                                                'synonyms/entry '
                                                                                                'allocated '
                                                                                                'for '
                                                                                                'it '
                                                                                                'are '
                                                                                                'rolled '
                                                                                                'back '
                                                                                                '— '
                                                                                                'use '
                                                                                                'this '
                                                                                                'when '
                                                                                                'the '
                                                                                                'synonyms '
                                                                                                'are '
                                                                                                'load-bearing '
                                                                                                '(e.g. '
                                                                                                'you '
                                                                                                'rely '
                                                                                                'on '
                                                                                                'original '
                                                                                                'IDs '
                                                                                                'to '
                                                                                                'resolve '
                                                                                                'after '
                                                                                                'import). '
                                                                                                "'warn': "
                                                                                                'the '
                                                                                                'document '
                                                                                                'is '
                                                                                                'created '
                                                                                                'and '
                                                                                                'the '
                                                                                                'refused '
                                                                                                'synonyms '
                                                                                                'are '
                                                                                                'reported '
                                                                                                'in '
                                                                                                '`warnings`.',
                                                                                 'default': 'fail'}},
                                          'additionalProperties': False,
                                          'type': 'object',
                                          'required': ['template_id', 'namespace', 'data'],
                                          'description': 'Request to create or update a document.'},
 'template-store#CreateTemplateRequest': {'properties': {'value': {'type': 'string',
                                                                   'description': 'Human-readable '
                                                                                  'value (e.g., '
                                                                                  "'PERSON')"},
                                                         'label': {'type': 'string',
                                                                   'description': 'Display label'},
                                                         'description': {'type': 'string',
                                                                         'description': 'Detailed '
                                                                                        'description'},
                                                         'template_id': {'type': 'string',
                                                                         'description': 'Pre-assigned '
                                                                                        'template '
                                                                                        'ID (for '
                                                                                        'restore/migration '
                                                                                        '— '
                                                                                        'Registry '
                                                                                        'uses '
                                                                                        'as-is '
                                                                                        'instead '
                                                                                        'of '
                                                                                        'generating)'},
                                                         'version': {'type': 'integer',
                                                                     'description': 'Pre-assigned '
                                                                                    'version (for '
                                                                                    'restore/migration, '
                                                                                    'with '
                                                                                    'template_id): '
                                                                                    'skips the '
                                                                                    'existing-value '
                                                                                    'check and '
                                                                                    'version '
                                                                                    'computation; '
                                                                                    'the identity '
                                                                                    'is still '
                                                                                    'registered '
                                                                                    'with the '
                                                                                    'Registry'},
                                                         'namespace': {'type': 'string',
                                                                       'description': 'Namespace '
                                                                                      'for the '
                                                                                      'template'},
                                                         'extends': {'type': 'string',
                                                                     'description': 'Parent '
                                                                                    'template ID '
                                                                                    'for '
                                                                                    'inheritance'},
                                                         'extends_version': {'type': 'integer',
                                                                             'description': 'Pinned '
                                                                                            'parent '
                                                                                            'version. '
                                                                                            'Required '
                                                                                            '(non-null) '
                                                                                            'whenever '
                                                                                            'the '
                                                                                            'template '
                                                                                            'declares '
                                                                                            "'extends' "
                                                                                            '— a '
                                                                                            'null '
                                                                                            'there '
                                                                                            'is '
                                                                                            'rejected; '
                                                                                            'the '
                                                                                            'parent '
                                                                                            'is '
                                                                                            'validated '
                                                                                            'against '
                                                                                            'this '
                                                                                            'exact '
                                                                                            'pinned '
                                                                                            'version, '
                                                                                            'never '
                                                                                            "'latest'. "
                                                                                            'Null '
                                                                                            'only '
                                                                                            'when '
                                                                                            'there '
                                                                                            'is no '
                                                                                            'parent.'},
                                                         'identity_fields': {'items': {'type': 'string'},
                                                                             'type': 'array',
                                                                             'description': 'Fields '
                                                                                            'that '
                                                                                            'form '
                                                                                            'the '
                                                                                            'composite '
                                                                                            'identity '
                                                                                            'key'},
                                                         'header_fields': {'items': {'type': 'string'},
                                                                           'type': 'array',
                                                                           'description': 'Fields '
                                                                                          'to '
                                                                                          'include '
                                                                                          'in '
                                                                                          'peer/header '
                                                                                          'projections. '
                                                                                          'Bare '
                                                                                          'names → '
                                                                                          'data.<name>; '
                                                                                          'metadata.custom.<name> '
                                                                                          'paths '
                                                                                          'allowed. '
                                                                                          'Empty → '
                                                                                          'projection '
                                                                                          'falls '
                                                                                          'back to '
                                                                                          'identity_fields.'},
                                                         'renames': {'additionalProperties': {'type': 'string'},
                                                                     'type': 'object',
                                                                     'description': 'Field renames '
                                                                                    'relative to '
                                                                                    'the previous '
                                                                                    'version, '
                                                                                    '{new_field: '
                                                                                    'old_field}. '
                                                                                    'Only '
                                                                                    'meaningful '
                                                                                    'when this '
                                                                                    'create '
                                                                                    'versions an '
                                                                                    'existing '
                                                                                    'template '
                                                                                    '(create is an '
                                                                                    'upsert); '
                                                                                    'rejected on a '
                                                                                    'first '
                                                                                    'version. '
                                                                                    'Identity '
                                                                                    'fields cannot '
                                                                                    'be renamed. '
                                                                                    'Declared '
                                                                                    'renames '
                                                                                    'migrate '
                                                                                    'losslessly '
                                                                                    'and map in '
                                                                                    'reporting.'},
                                                         'usage': {'type': 'string',
                                                                   'enum': ['entity',
                                                                            'reference',
                                                                            'relationship'],
                                                                   'description': 'How a '
                                                                                  "template's "
                                                                                  'documents are '
                                                                                  'intended to be '
                                                                                  'used.\n'
                                                                                  '\n'
                                                                                  '- entity '
                                                                                  '(default): full '
                                                                                  'document '
                                                                                  'lifecycle, the '
                                                                                  'v1.x '
                                                                                  'behaviour.\n'
                                                                                  '- reference: '
                                                                                  'lightweight '
                                                                                  'controlled-vocabulary '
                                                                                  'documents '
                                                                                  '(LOV).\n'
                                                                                  '  Reserved for '
                                                                                  'a future phase; '
                                                                                  'currently '
                                                                                  'behaves like '
                                                                                  'entity.\n'
                                                                                  '- relationship: '
                                                                                  'typed, '
                                                                                  'property-carrying '
                                                                                  'edge between '
                                                                                  'two\n'
                                                                                  '  documents. '
                                                                                  'Requires '
                                                                                  'source_templates '
                                                                                  '/ '
                                                                                  'target_templates '
                                                                                  'to be set\n'
                                                                                  '  and a '
                                                                                  'source_ref / '
                                                                                  'target_ref '
                                                                                  'reference field '
                                                                                  'on the '
                                                                                  'template.\n'
                                                                                  '  See '
                                                                                  'docs/design/document-relationships.md.'},
                                                         'source_templates': {'items': {'anyOf': [{'type': 'string'},
                                                                                                  {'properties': {'lookup_value': {'type': 'string',
                                                                                                                                   'description': 'The '
                                                                                                                                                  'endpoint '
                                                                                                                                                  'reference '
                                                                                                                                                  '— '
                                                                                                                                                  'template '
                                                                                                                                                  'value, '
                                                                                                                                                  'ns:VALUE, '
                                                                                                                                                  'or '
                                                                                                                                                  'canonical '
                                                                                                                                                  'id'},
                                                                                                                  'resolved': {'type': 'string',
                                                                                                                               'description': 'Ignored '
                                                                                                                                              'on '
                                                                                                                                              'write '
                                                                                                                                              '— '
                                                                                                                                              'the '
                                                                                                                                              'platform '
                                                                                                                                              'resolves '
                                                                                                                                              'lookup_value '
                                                                                                                                              'itself'}},
                                                                                                   'additionalProperties': False,
                                                                                                   'type': 'object',
                                                                                                   'required': ['lookup_value'],
                                                                                                   'description': 'An '
                                                                                                                  'edge-type '
                                                                                                                  'endpoint '
                                                                                                                  'as '
                                                                                                                  'a '
                                                                                                                  'caller '
                                                                                                                  'may '
                                                                                                                  'submit '
                                                                                                                  'it.\n'
                                                                                                                  '\n'
                                                                                                                  'Only '
                                                                                                                  '``lookup_value`` '
                                                                                                                  'is '
                                                                                                                  'honored '
                                                                                                                  '— '
                                                                                                                  '``resolved`` '
                                                                                                                  'is '
                                                                                                                  'server-owned '
                                                                                                                  'and '
                                                                                                                  'is\n'
                                                                                                                  'recomputed '
                                                                                                                  'from '
                                                                                                                  'the '
                                                                                                                  'lookup '
                                                                                                                  'at '
                                                                                                                  'ingress, '
                                                                                                                  'so '
                                                                                                                  'a '
                                                                                                                  'GET '
                                                                                                                  '→ '
                                                                                                                  'POST '
                                                                                                                  'round-trip '
                                                                                                                  'of '
                                                                                                                  'a\n'
                                                                                                                  'served '
                                                                                                                  'template '
                                                                                                                  'is '
                                                                                                                  'valid '
                                                                                                                  'input '
                                                                                                                  'without '
                                                                                                                  'letting '
                                                                                                                  'a '
                                                                                                                  'caller '
                                                                                                                  'pin '
                                                                                                                  'a '
                                                                                                                  'stale '
                                                                                                                  'or\n'
                                                                                                                  'foreign '
                                                                                                                  'canonical '
                                                                                                                  'id. '
                                                                                                                  'Plain '
                                                                                                                  'strings '
                                                                                                                  'are '
                                                                                                                  'accepted '
                                                                                                                  'wherever '
                                                                                                                  'this '
                                                                                                                  'model '
                                                                                                                  'is\n'
                                                                                                                  '(a '
                                                                                                                  'bare '
                                                                                                                  'value, '
                                                                                                                  '``ns:VALUE``, '
                                                                                                                  'or '
                                                                                                                  'an '
                                                                                                                  'id), '
                                                                                                                  'so '
                                                                                                                  'existing '
                                                                                                                  'callers '
                                                                                                                  'are\n'
                                                                                                                  'unaffected.'}]},
                                                                              'type': 'array',
                                                                              'description': 'Templates '
                                                                                             'allowed '
                                                                                             'as '
                                                                                             'edge '
                                                                                             'source '
                                                                                             '(required '
                                                                                             'when '
                                                                                             'usage=relationship; '
                                                                                             'rejected '
                                                                                             'with '
                                                                                             'an '
                                                                                             'error '
                                                                                             'on '
                                                                                             'non-relationship '
                                                                                             'templates). '
                                                                                             'Each '
                                                                                             'entry '
                                                                                             'is a '
                                                                                             'template '
                                                                                             'value, '
                                                                                             'a '
                                                                                             'canonical '
                                                                                             'template_id, '
                                                                                             'ns:VALUE, '
                                                                                             'or a '
                                                                                             'full '
                                                                                             '{lookup_value, '
                                                                                             'resolved} '
                                                                                             'entry; '
                                                                                             'stored '
                                                                                             'and '
                                                                                             'returned '
                                                                                             'as '
                                                                                             'two-half '
                                                                                             'entries '
                                                                                             'whose '
                                                                                             'resolved '
                                                                                             'half '
                                                                                             'the '
                                                                                             'platform '
                                                                                             'fills'},
                                                         'target_templates': {'items': {'anyOf': [{'type': 'string'},
                                                                                                  {'properties': {'lookup_value': {'type': 'string',
                                                                                                                                   'description': 'The '
                                                                                                                                                  'endpoint '
                                                                                                                                                  'reference '
                                                                                                                                                  '— '
                                                                                                                                                  'template '
                                                                                                                                                  'value, '
                                                                                                                                                  'ns:VALUE, '
                                                                                                                                                  'or '
                                                                                                                                                  'canonical '
                                                                                                                                                  'id'},
                                                                                                                  'resolved': {'type': 'string',
                                                                                                                               'description': 'Ignored '
                                                                                                                                              'on '
                                                                                                                                              'write '
                                                                                                                                              '— '
                                                                                                                                              'the '
                                                                                                                                              'platform '
                                                                                                                                              'resolves '
                                                                                                                                              'lookup_value '
                                                                                                                                              'itself'}},
                                                                                                   'additionalProperties': False,
                                                                                                   'type': 'object',
                                                                                                   'required': ['lookup_value'],
                                                                                                   'description': 'An '
                                                                                                                  'edge-type '
                                                                                                                  'endpoint '
                                                                                                                  'as '
                                                                                                                  'a '
                                                                                                                  'caller '
                                                                                                                  'may '
                                                                                                                  'submit '
                                                                                                                  'it.\n'
                                                                                                                  '\n'
                                                                                                                  'Only '
                                                                                                                  '``lookup_value`` '
                                                                                                                  'is '
                                                                                                                  'honored '
                                                                                                                  '— '
                                                                                                                  '``resolved`` '
                                                                                                                  'is '
                                                                                                                  'server-owned '
                                                                                                                  'and '
                                                                                                                  'is\n'
                                                                                                                  'recomputed '
                                                                                                                  'from '
                                                                                                                  'the '
                                                                                                                  'lookup '
                                                                                                                  'at '
                                                                                                                  'ingress, '
                                                                                                                  'so '
                                                                                                                  'a '
                                                                                                                  'GET '
                                                                                                                  '→ '
                                                                                                                  'POST '
                                                                                                                  'round-trip '
                                                                                                                  'of '
                                                                                                                  'a\n'
                                                                                                                  'served '
                                                                                                                  'template '
                                                                                                                  'is '
                                                                                                                  'valid '
                                                                                                                  'input '
                                                                                                                  'without '
                                                                                                                  'letting '
                                                                                                                  'a '
                                                                                                                  'caller '
                                                                                                                  'pin '
                                                                                                                  'a '
                                                                                                                  'stale '
                                                                                                                  'or\n'
                                                                                                                  'foreign '
                                                                                                                  'canonical '
                                                                                                                  'id. '
                                                                                                                  'Plain '
                                                                                                                  'strings '
                                                                                                                  'are '
                                                                                                                  'accepted '
                                                                                                                  'wherever '
                                                                                                                  'this '
                                                                                                                  'model '
                                                                                                                  'is\n'
                                                                                                                  '(a '
                                                                                                                  'bare '
                                                                                                                  'value, '
                                                                                                                  '``ns:VALUE``, '
                                                                                                                  'or '
                                                                                                                  'an '
                                                                                                                  'id), '
                                                                                                                  'so '
                                                                                                                  'existing '
                                                                                                                  'callers '
                                                                                                                  'are\n'
                                                                                                                  'unaffected.'}]},
                                                                              'type': 'array',
                                                                              'description': 'Templates '
                                                                                             'allowed '
                                                                                             'as '
                                                                                             'edge '
                                                                                             'target '
                                                                                             '(required '
                                                                                             'when '
                                                                                             'usage=relationship; '
                                                                                             'rejected '
                                                                                             'with '
                                                                                             'an '
                                                                                             'error '
                                                                                             'on '
                                                                                             'non-relationship '
                                                                                             'templates). '
                                                                                             'Each '
                                                                                             'entry '
                                                                                             'is a '
                                                                                             'template '
                                                                                             'value, '
                                                                                             'a '
                                                                                             'canonical '
                                                                                             'template_id, '
                                                                                             'ns:VALUE, '
                                                                                             'or a '
                                                                                             'full '
                                                                                             '{lookup_value, '
                                                                                             'resolved} '
                                                                                             'entry; '
                                                                                             'stored '
                                                                                             'and '
                                                                                             'returned '
                                                                                             'as '
                                                                                             'two-half '
                                                                                             'entries '
                                                                                             'whose '
                                                                                             'resolved '
                                                                                             'half '
                                                                                             'the '
                                                                                             'platform '
                                                                                             'fills'},
                                                         'versioned': {'type': 'boolean',
                                                                       'description': 'True = '
                                                                                      'updates '
                                                                                      'create new '
                                                                                      'versions; '
                                                                                      'False = '
                                                                                      'overwrite '
                                                                                      'in place. '
                                                                                      'Immutable '
                                                                                      'after '
                                                                                      'creation.',
                                                                       'default': True},
                                                         'fields': {'items': {'properties': {'name': {'type': 'string',
                                                                                                      'description': 'Field '
                                                                                                                     'name '
                                                                                                                     '(used '
                                                                                                                     'in '
                                                                                                                     'data)',
                                                                                                      'examples': ['first_name',
                                                                                                                   'birth_date']},
                                                                                             'label': {'type': 'string',
                                                                                                       'description': 'Human-readable '
                                                                                                                      'label',
                                                                                                       'examples': ['First '
                                                                                                                    'Name',
                                                                                                                    'Date '
                                                                                                                    'of '
                                                                                                                    'Birth']},
                                                                                             'type': {'type': 'string',
                                                                                                      'enum': ['string',
                                                                                                               'number',
                                                                                                               'integer',
                                                                                                               'boolean',
                                                                                                               'date',
                                                                                                               'datetime',
                                                                                                               'term',
                                                                                                               'reference',
                                                                                                               'file',
                                                                                                               'object',
                                                                                                               'array'],
                                                                                                      'description': 'Supported '
                                                                                                                     'field '
                                                                                                                     'types '
                                                                                                                     'for '
                                                                                                                     'template '
                                                                                                                     'fields.'},
                                                                                             'mandatory': {'type': 'boolean',
                                                                                                           'description': 'Whether '
                                                                                                                          'field '
                                                                                                                          'is '
                                                                                                                          'required',
                                                                                                           'default': False},
                                                                                             'default_value': {'description': 'A '
                                                                                                                              'suggested '
                                                                                                                              'initial '
                                                                                                                              'value, '
                                                                                                                              'for '
                                                                                                                              'clients '
                                                                                                                              '(a '
                                                                                                                              "form's "
                                                                                                                              'prefill). '
                                                                                                                              'Informational '
                                                                                                                              'only: '
                                                                                                                              'the '
                                                                                                                              'platform '
                                                                                                                              'never '
                                                                                                                              'fills '
                                                                                                                              'an '
                                                                                                                              'omitted '
                                                                                                                              'field '
                                                                                                                              'with '
                                                                                                                              'it, '
                                                                                                                              'so '
                                                                                                                              'a '
                                                                                                                              'mandatory '
                                                                                                                              'field '
                                                                                                                              'with '
                                                                                                                              'a '
                                                                                                                              'default '
                                                                                                                              'is '
                                                                                                                              'still '
                                                                                                                              'required '
                                                                                                                              'on '
                                                                                                                              'every '
                                                                                                                              'write.'},
                                                                                             'terminology_ref': {'type': 'string',
                                                                                                                 'description': 'Terminology '
                                                                                                                                'for '
                                                                                                                                'term '
                                                                                                                                'validation: '
                                                                                                                                'stored '
                                                                                                                                'as '
                                                                                                                                'its '
                                                                                                                                'canonical '
                                                                                                                                'terminology_id '
                                                                                                                                'once '
                                                                                                                                'the '
                                                                                                                                'template '
                                                                                                                                'is '
                                                                                                                                'active '
                                                                                                                                '(a '
                                                                                                                                'draft '
                                                                                                                                'keeps '
                                                                                                                                'the '
                                                                                                                                'submitted '
                                                                                                                                'form '
                                                                                                                                'until '
                                                                                                                                'activation)'},
                                                                                             'template_ref': {'type': 'string',
                                                                                                              'description': 'Template '
                                                                                                                             'for '
                                                                                                                             'the '
                                                                                                                             'nested '
                                                                                                                             'object: '
                                                                                                                             'stored '
                                                                                                                             'as '
                                                                                                                             'its '
                                                                                                                             'canonical '
                                                                                                                             'template_id '
                                                                                                                             'once '
                                                                                                                             'the '
                                                                                                                             'template '
                                                                                                                             'is '
                                                                                                                             'active '
                                                                                                                             '(a '
                                                                                                                             'draft '
                                                                                                                             'keeps '
                                                                                                                             'the '
                                                                                                                             'submitted '
                                                                                                                             'form '
                                                                                                                             'until '
                                                                                                                             'activation)'},
                                                                                             'template_ref_version': {'type': 'integer',
                                                                                                                      'description': 'Pinned '
                                                                                                                                     'version '
                                                                                                                                     'of '
                                                                                                                                     'the '
                                                                                                                                     'nested '
                                                                                                                                     'template '
                                                                                                                                     '(mandatory '
                                                                                                                                     'when '
                                                                                                                                     'template_ref '
                                                                                                                                     'is '
                                                                                                                                     'set)'},
                                                                                             'reference_type': {'type': 'string',
                                                                                                                'enum': ['document',
                                                                                                                         'term',
                                                                                                                         'terminology',
                                                                                                                         'template'],
                                                                                                                'description': 'Type '
                                                                                                                               'of '
                                                                                                                               'entity '
                                                                                                                               'being '
                                                                                                                               'referenced '
                                                                                                                               '(for '
                                                                                                                               'reference '
                                                                                                                               'type)'},
                                                                                             'target_templates': {'items': {'type': 'string'},
                                                                                                                  'type': 'array',
                                                                                                                  'description': 'Allowed '
                                                                                                                                 'document '
                                                                                                                                 'reference '
                                                                                                                                 'targets: '
                                                                                                                                 'stored '
                                                                                                                                 'as '
                                                                                                                                 'canonical '
                                                                                                                                 'template_ids '
                                                                                                                                 'once '
                                                                                                                                 'the '
                                                                                                                                 'template '
                                                                                                                                 'is '
                                                                                                                                 'active '
                                                                                                                                 '(a '
                                                                                                                                 'draft '
                                                                                                                                 'keeps '
                                                                                                                                 'the '
                                                                                                                                 'submitted '
                                                                                                                                 'forms '
                                                                                                                                 'until '
                                                                                                                                 'activation). '
                                                                                                                                 'On '
                                                                                                                                 'an '
                                                                                                                                 'edge '
                                                                                                                                 "type's "
                                                                                                                                 'source_ref '
                                                                                                                                 '/ '
                                                                                                                                 'target_ref '
                                                                                                                                 'this '
                                                                                                                                 'is '
                                                                                                                                 'derived '
                                                                                                                                 'from '
                                                                                                                                 'the '
                                                                                                                                 'template-level '
                                                                                                                                 'declaration '
                                                                                                                                 'when '
                                                                                                                                 'served, '
                                                                                                                                 'and '
                                                                                                                                 'discarded '
                                                                                                                                 'on '
                                                                                                                                 'write'},
                                                                                             'include_subtypes': {'type': 'boolean',
                                                                                                                  'description': 'When '
                                                                                                                                 'true, '
                                                                                                                                 'target_templates '
                                                                                                                                 'also '
                                                                                                                                 'accepts '
                                                                                                                                 'documents '
                                                                                                                                 'from '
                                                                                                                                 'child '
                                                                                                                                 'templates '
                                                                                                                                 '(via '
                                                                                                                                 'inheritance)'},
                                                                                             'target_terminologies': {'items': {'type': 'string'},
                                                                                                                      'type': 'array',
                                                                                                                      'description': 'Allowed '
                                                                                                                                     'term '
                                                                                                                                     'reference '
                                                                                                                                     'targets: '
                                                                                                                                     'stored '
                                                                                                                                     'as '
                                                                                                                                     'canonical '
                                                                                                                                     'terminology_ids '
                                                                                                                                     'once '
                                                                                                                                     'the '
                                                                                                                                     'template '
                                                                                                                                     'is '
                                                                                                                                     'active '
                                                                                                                                     '(a '
                                                                                                                                     'draft '
                                                                                                                                     'keeps '
                                                                                                                                     'the '
                                                                                                                                     'submitted '
                                                                                                                                     'forms '
                                                                                                                                     'until '
                                                                                                                                     'activation)'},
                                                                                             'version_strategy': {'type': 'string',
                                                                                                                  'enum': ['latest',
                                                                                                                           'pinned'],
                                                                                                                  'description': 'How '
                                                                                                                                 'to '
                                                                                                                                 'resolve '
                                                                                                                                 'reference '
                                                                                                                                 'versions '
                                                                                                                                 '(default: '
                                                                                                                                 'latest)'},
                                                                                             'file_config': {'properties': {'allowed_types': {'items': {'type': 'string'},
                                                                                                                                              'type': 'array',
                                                                                                                                              'description': 'Allowed '
                                                                                                                                                             'MIME '
                                                                                                                                                             'type '
                                                                                                                                                             'patterns '
                                                                                                                                                             '(e.g., '
                                                                                                                                                             "'image/*', "
                                                                                                                                                             "'application/pdf'). "
                                                                                                                                                             'Must '
                                                                                                                                                             'not '
                                                                                                                                                             'be '
                                                                                                                                                             'empty; '
                                                                                                                                                             'omit '
                                                                                                                                                             'it '
                                                                                                                                                             'to '
                                                                                                                                                             'accept '
                                                                                                                                                             'every '
                                                                                                                                                             'type.',
                                                                                                                                              'default': ['*/*']},
                                                                                                                            'max_size_mb': {'type': 'number',
                                                                                                                                            'maximum': 100.0,
                                                                                                                                            'exclusiveMinimum': 0.0,
                                                                                                                                            'description': 'Maximum '
                                                                                                                                                           'file '
                                                                                                                                                           'size '
                                                                                                                                                           'in '
                                                                                                                                                           'MB '
                                                                                                                                                           '(max '
                                                                                                                                                           '100MB)',
                                                                                                                                            'default': 10.0},
                                                                                                                            'multiple': {'type': 'boolean',
                                                                                                                                         'description': 'Allow '
                                                                                                                                                        'multiple '
                                                                                                                                                        'files '
                                                                                                                                                        '(field '
                                                                                                                                                        'value '
                                                                                                                                                        'becomes '
                                                                                                                                                        'array '
                                                                                                                                                        'of '
                                                                                                                                                        'file '
                                                                                                                                                        'IDs)',
                                                                                                                                         'default': False},
                                                                                                                            'max_files': {'type': 'integer',
                                                                                                                                          'maximum': 100.0,
                                                                                                                                          'minimum': 1.0,
                                                                                                                                          'description': 'Maximum '
                                                                                                                                                         'number '
                                                                                                                                                         'of '
                                                                                                                                                         'files '
                                                                                                                                                         'when '
                                                                                                                                                         'multiple=true '
                                                                                                                                                         '(default: '
                                                                                                                                                         'unlimited)'}},
                                                                                                             'type': 'object',
                                                                                                             'description': 'Configuration '
                                                                                                                            'for '
                                                                                                                            'file '
                                                                                                                            'fields '
                                                                                                                            '(allowed '
                                                                                                                            'types, '
                                                                                                                            'size '
                                                                                                                            'limits)'},
                                                                                             'array_item_type': {'type': 'string',
                                                                                                                 'enum': ['string',
                                                                                                                          'number',
                                                                                                                          'integer',
                                                                                                                          'boolean',
                                                                                                                          'date',
                                                                                                                          'datetime',
                                                                                                                          'term',
                                                                                                                          'reference',
                                                                                                                          'file',
                                                                                                                          'object',
                                                                                                                          'array'],
                                                                                                                 'description': 'Type '
                                                                                                                                'of '
                                                                                                                                'array '
                                                                                                                                'items '
                                                                                                                                '(for '
                                                                                                                                'array '
                                                                                                                                'type)'},
                                                                                             'array_terminology_ref': {'type': 'string',
                                                                                                                       'description': 'Terminology '
                                                                                                                                      'for '
                                                                                                                                      'array '
                                                                                                                                      'item '
                                                                                                                                      'term '
                                                                                                                                      'validation: '
                                                                                                                                      'stored '
                                                                                                                                      'as '
                                                                                                                                      'its '
                                                                                                                                      'canonical '
                                                                                                                                      'terminology_id '
                                                                                                                                      'once '
                                                                                                                                      'the '
                                                                                                                                      'template '
                                                                                                                                      'is '
                                                                                                                                      'active '
                                                                                                                                      '(a '
                                                                                                                                      'draft '
                                                                                                                                      'keeps '
                                                                                                                                      'the '
                                                                                                                                      'submitted '
                                                                                                                                      'form '
                                                                                                                                      'until '
                                                                                                                                      'activation)'},
                                                                                             'array_template_ref': {'type': 'string',
                                                                                                                    'description': 'Template '
                                                                                                                                   'for '
                                                                                                                                   'array '
                                                                                                                                   'items: '
                                                                                                                                   'stored '
                                                                                                                                   'as '
                                                                                                                                   'its '
                                                                                                                                   'canonical '
                                                                                                                                   'template_id '
                                                                                                                                   'once '
                                                                                                                                   'the '
                                                                                                                                   'template '
                                                                                                                                   'is '
                                                                                                                                   'active '
                                                                                                                                   '(a '
                                                                                                                                   'draft '
                                                                                                                                   'keeps '
                                                                                                                                   'the '
                                                                                                                                   'submitted '
                                                                                                                                   'form '
                                                                                                                                   'until '
                                                                                                                                   'activation)'},
                                                                                             'array_template_ref_version': {'type': 'integer',
                                                                                                                            'description': 'Pinned '
                                                                                                                                           'version '
                                                                                                                                           'of '
                                                                                                                                           'the '
                                                                                                                                           'array-item '
                                                                                                                                           'template '
                                                                                                                                           '(mandatory '
                                                                                                                                           'when '
                                                                                                                                           'array_template_ref '
                                                                                                                                           'is '
                                                                                                                                           'set)'},
                                                                                             'array_file_config': {'properties': {'allowed_types': {'items': {'type': 'string'},
                                                                                                                                                    'type': 'array',
                                                                                                                                                    'description': 'Allowed '
                                                                                                                                                                   'MIME '
                                                                                                                                                                   'type '
                                                                                                                                                                   'patterns '
                                                                                                                                                                   '(e.g., '
                                                                                                                                                                   "'image/*', "
                                                                                                                                                                   "'application/pdf'). "
                                                                                                                                                                   'Must '
                                                                                                                                                                   'not '
                                                                                                                                                                   'be '
                                                                                                                                                                   'empty; '
                                                                                                                                                                   'omit '
                                                                                                                                                                   'it '
                                                                                                                                                                   'to '
                                                                                                                                                                   'accept '
                                                                                                                                                                   'every '
                                                                                                                                                                   'type.',
                                                                                                                                                    'default': ['*/*']},
                                                                                                                                  'max_size_mb': {'type': 'number',
                                                                                                                                                  'maximum': 100.0,
                                                                                                                                                  'exclusiveMinimum': 0.0,
                                                                                                                                                  'description': 'Maximum '
                                                                                                                                                                 'file '
                                                                                                                                                                 'size '
                                                                                                                                                                 'in '
                                                                                                                                                                 'MB '
                                                                                                                                                                 '(max '
                                                                                                                                                                 '100MB)',
                                                                                                                                                  'default': 10.0},
                                                                                                                                  'multiple': {'type': 'boolean',
                                                                                                                                               'description': 'Allow '
                                                                                                                                                              'multiple '
                                                                                                                                                              'files '
                                                                                                                                                              '(field '
                                                                                                                                                              'value '
                                                                                                                                                              'becomes '
                                                                                                                                                              'array '
                                                                                                                                                              'of '
                                                                                                                                                              'file '
                                                                                                                                                              'IDs)',
                                                                                                                                               'default': False},
                                                                                                                                  'max_files': {'type': 'integer',
                                                                                                                                                'maximum': 100.0,
                                                                                                                                                'minimum': 1.0,
                                                                                                                                                'description': 'Maximum '
                                                                                                                                                               'number '
                                                                                                                                                               'of '
                                                                                                                                                               'files '
                                                                                                                                                               'when '
                                                                                                                                                               'multiple=true '
                                                                                                                                                               '(default: '
                                                                                                                                                               'unlimited)'}},
                                                                                                                   'type': 'object',
                                                                                                                   'description': 'File '
                                                                                                                                  'configuration '
                                                                                                                                  'for '
                                                                                                                                  'array '
                                                                                                                                  'items '
                                                                                                                                  'if '
                                                                                                                                  'file '
                                                                                                                                  'type'},
                                                                                             'validation': {'properties': {'pattern': {'type': 'string',
                                                                                                                                       'description': 'Regex '
                                                                                                                                                      'pattern '
                                                                                                                                                      'for '
                                                                                                                                                      'string '
                                                                                                                                                      'fields; '
                                                                                                                                                      'must '
                                                                                                                                                      'match '
                                                                                                                                                      'the '
                                                                                                                                                      'whole '
                                                                                                                                                      'value '
                                                                                                                                                      '(no '
                                                                                                                                                      '^/$ '
                                                                                                                                                      'anchors '
                                                                                                                                                      'needed)'},
                                                                                                                           'min_length': {'type': 'integer',
                                                                                                                                          'description': 'Minimum '
                                                                                                                                                         'string '
                                                                                                                                                         'length'},
                                                                                                                           'max_length': {'type': 'integer',
                                                                                                                                          'description': 'Maximum '
                                                                                                                                                         'string '
                                                                                                                                                         'length'},
                                                                                                                           'minimum': {'type': 'number',
                                                                                                                                       'description': 'Minimum '
                                                                                                                                                      'numeric '
                                                                                                                                                      'value'},
                                                                                                                           'maximum': {'type': 'number',
                                                                                                                                       'description': 'Maximum '
                                                                                                                                                      'numeric '
                                                                                                                                                      'value'},
                                                                                                                           'enum': {'items': {},
                                                                                                                                    'type': 'array',
                                                                                                                                    'description': 'Allowed '
                                                                                                                                                   'values '
                                                                                                                                                   '(not '
                                                                                                                                                   'term-based)'}},
                                                                                                            'type': 'object',
                                                                                                            'description': 'Field-level '
                                                                                                                           'validation '
                                                                                                                           'rules'},
                                                                                             'semantic_type': {'type': 'string',
                                                                                                               'enum': ['email',
                                                                                                                        'url',
                                                                                                                        'latitude',
                                                                                                                        'longitude',
                                                                                                                        'percentage',
                                                                                                                        'duration',
                                                                                                                        'geo_point'],
                                                                                                               'description': 'Semantic '
                                                                                                                              'type '
                                                                                                                              'for '
                                                                                                                              'additional '
                                                                                                                              'validation '
                                                                                                                              '(email, '
                                                                                                                              'url, '
                                                                                                                              'latitude, '
                                                                                                                              'etc.)'},
                                                                                             'full_text_indexed': {'type': 'boolean',
                                                                                                                   'description': 'Enable '
                                                                                                                                  'PostgreSQL '
                                                                                                                                  'full-text '
                                                                                                                                  'indexing '
                                                                                                                                  'on '
                                                                                                                                  'this '
                                                                                                                                  'field. '
                                                                                                                                  'Only '
                                                                                                                                  'valid '
                                                                                                                                  'for '
                                                                                                                                  'type=string '
                                                                                                                                  'and '
                                                                                                                                  'requires '
                                                                                                                                  'template '
                                                                                                                                  'reporting.sync_enabled=true. '
                                                                                                                                  'Future: '
                                                                                                                                  'language '
                                                                                                                                  'codes '
                                                                                                                                  "('en', "
                                                                                                                                  "'de') "
                                                                                                                                  'will '
                                                                                                                                  'be '
                                                                                                                                  'accepted; '
                                                                                                                                  'v1 '
                                                                                                                                  'is '
                                                                                                                                  'bool '
                                                                                                                                  'only.'},
                                                                                             'inherited': {'type': 'boolean',
                                                                                                           'description': 'Whether '
                                                                                                                          'this '
                                                                                                                          'field '
                                                                                                                          'is '
                                                                                                                          'inherited '
                                                                                                                          'from '
                                                                                                                          'a '
                                                                                                                          'parent '
                                                                                                                          'template '
                                                                                                                          '(set '
                                                                                                                          'during '
                                                                                                                          'resolution)'},
                                                                                             'inherited_from': {'type': 'string',
                                                                                                                'description': 'Template '
                                                                                                                               'ID '
                                                                                                                               'of '
                                                                                                                               'the '
                                                                                                                               'parent '
                                                                                                                               'template '
                                                                                                                               'this '
                                                                                                                               'field '
                                                                                                                               'was '
                                                                                                                               'inherited '
                                                                                                                               'from'},
                                                                                             'metadata': {'additionalProperties': True,
                                                                                                          'type': 'object',
                                                                                                          'description': 'Additional '
                                                                                                                         'field '
                                                                                                                         'metadata'}},
                                                                              'type': 'object',
                                                                              'required': ['name',
                                                                                           'label',
                                                                                           'type'],
                                                                              'description': 'A '
                                                                                             'field '
                                                                                             'definition '
                                                                                             'within '
                                                                                             'a '
                                                                                             'template.'},
                                                                    'type': 'array',
                                                                    'description': 'Field '
                                                                                   'definitions'},
                                                         'rules': {'items': {'properties': {'type': {'type': 'string',
                                                                                                     'enum': ['conditional_required',
                                                                                                              'conditional_value',
                                                                                                              'mutual_exclusion',
                                                                                                              'dependency',
                                                                                                              'pattern',
                                                                                                              'range'],
                                                                                                     'description': 'Types '
                                                                                                                    'of '
                                                                                                                    'cross-field '
                                                                                                                    'validation '
                                                                                                                    'rules.'},
                                                                                            'description': {'type': 'string',
                                                                                                            'description': 'Human-readable '
                                                                                                                           'description '
                                                                                                                           'of '
                                                                                                                           'the '
                                                                                                                           'rule'},
                                                                                            'conditions': {'items': {'properties': {'field': {'type': 'string',
                                                                                                                                              'description': 'Field '
                                                                                                                                                             'to '
                                                                                                                                                             'check'},
                                                                                                                                    'operator': {'type': 'string',
                                                                                                                                                 'enum': ['equals',
                                                                                                                                                          'not_equals',
                                                                                                                                                          'in',
                                                                                                                                                          'not_in',
                                                                                                                                                          'exists',
                                                                                                                                                          'not_exists'],
                                                                                                                                                 'description': 'Comparison '
                                                                                                                                                                'operator'},
                                                                                                                                    'value': {'description': 'Value '
                                                                                                                                                             'to '
                                                                                                                                                             'compare '
                                                                                                                                                             '(not '
                                                                                                                                                             'needed '
                                                                                                                                                             'for '
                                                                                                                                                             'exists '
                                                                                                                                                             'operators)'}},
                                                                                                                     'type': 'object',
                                                                                                                     'required': ['field',
                                                                                                                                  'operator'],
                                                                                                                     'description': 'A '
                                                                                                                                    'condition '
                                                                                                                                    'for '
                                                                                                                                    'conditional '
                                                                                                                                    'rules.'},
                                                                                                           'type': 'array',
                                                                                                           'description': 'Conditions '
                                                                                                                          'that '
                                                                                                                          'trigger '
                                                                                                                          'the '
                                                                                                                          'rule'},
                                                                                            'target_field': {'type': 'string',
                                                                                                             'description': 'Field '
                                                                                                                            'affected '
                                                                                                                            'by '
                                                                                                                            'the '
                                                                                                                            'rule'},
                                                                                            'target_fields': {'items': {'type': 'string'},
                                                                                                              'type': 'array',
                                                                                                              'description': 'Fields '
                                                                                                                             'affected '
                                                                                                                             '(for '
                                                                                                                             'mutual_exclusion)'},
                                                                                            'required': {'type': 'boolean',
                                                                                                         'description': 'For '
                                                                                                                        'conditional_required: '
                                                                                                                        'is '
                                                                                                                        'field '
                                                                                                                        'required?'},
                                                                                            'allowed_values': {'items': {},
                                                                                                               'type': 'array',
                                                                                                               'description': 'For '
                                                                                                                              'conditional_value: '
                                                                                                                              'allowed '
                                                                                                                              'values'},
                                                                                            'pattern': {'type': 'string',
                                                                                                        'description': 'For '
                                                                                                                       'pattern: '
                                                                                                                       'regex '
                                                                                                                       'pattern'},
                                                                                            'minimum': {'type': 'number',
                                                                                                        'description': 'For '
                                                                                                                       'range: '
                                                                                                                       'minimum '
                                                                                                                       'value'},
                                                                                            'maximum': {'type': 'number',
                                                                                                        'description': 'For '
                                                                                                                       'range: '
                                                                                                                       'maximum '
                                                                                                                       'value'},
                                                                                            'error_message': {'type': 'string',
                                                                                                              'description': 'Custom '
                                                                                                                             'error '
                                                                                                                             'message'}},
                                                                             'type': 'object',
                                                                             'required': ['type'],
                                                                             'description': 'A '
                                                                                            'cross-field '
                                                                                            'validation '
                                                                                            'rule.'},
                                                                   'type': 'array',
                                                                   'description': 'Cross-field '
                                                                                  'validation '
                                                                                  'rules'},
                                                         'metadata': {'properties': {'domain': {'type': 'string',
                                                                                                'description': 'Business '
                                                                                                               'domain '
                                                                                                               '(e.g., '
                                                                                                               "'hr', "
                                                                                                               "'finance', "
                                                                                                               "'healthcare')"},
                                                                                     'category': {'type': 'string',
                                                                                                  'description': 'Template '
                                                                                                                 'category '
                                                                                                                 '(e.g., '
                                                                                                                 "'master_data', "
                                                                                                                 "'transaction')"},
                                                                                     'tags': {'items': {'type': 'string'},
                                                                                              'type': 'array',
                                                                                              'description': 'Tags '
                                                                                                             'for '
                                                                                                             'categorization '
                                                                                                             'and '
                                                                                                             'search'},
                                                                                     'custom': {'additionalProperties': True,
                                                                                                'type': 'object',
                                                                                                'description': 'Custom '
                                                                                                               'metadata '
                                                                                                               'fields'}},
                                                                      'type': 'object',
                                                                      'description': 'Additional '
                                                                                     'metadata'},
                                                         'reporting': {'properties': {'sync_enabled': {'type': 'boolean',
                                                                                                       'description': 'Whether '
                                                                                                                      'to '
                                                                                                                      'sync '
                                                                                                                      'documents '
                                                                                                                      'of '
                                                                                                                      'this '
                                                                                                                      'template '
                                                                                                                      'to '
                                                                                                                      'PostgreSQL',
                                                                                                       'default': True},
                                                                                      'sync_strategy': {'type': 'string',
                                                                                                        'description': 'Sync '
                                                                                                                       'strategy: '
                                                                                                                       "'latest_only' "
                                                                                                                       '(upsert '
                                                                                                                       'the '
                                                                                                                       'latest '
                                                                                                                       'version), '
                                                                                                                       "'all_versions' "
                                                                                                                       '(insert '
                                                                                                                       'every '
                                                                                                                       'version) '
                                                                                                                       'or '
                                                                                                                       "'disabled'",
                                                                                                        'default': 'latest_only'},
                                                                                      'table_name': {'type': 'string',
                                                                                                     'description': 'Custom '
                                                                                                                    'PostgreSQL '
                                                                                                                    'table '
                                                                                                                    'name '
                                                                                                                    'base '
                                                                                                                    '(doc_<value> '
                                                                                                                    'if '
                                                                                                                    'not '
                                                                                                                    'set). '
                                                                                                                    'Fixed '
                                                                                                                    'once '
                                                                                                                    'the '
                                                                                                                    'template '
                                                                                                                    'exists: '
                                                                                                                    'its '
                                                                                                                    'reporting '
                                                                                                                    'tables '
                                                                                                                    'are '
                                                                                                                    'named '
                                                                                                                    'from '
                                                                                                                    'it'},
                                                                                      'include_metadata': {'type': 'boolean',
                                                                                                           'description': 'Include '
                                                                                                                          'created_at, '
                                                                                                                          'created_by, '
                                                                                                                          'etc. '
                                                                                                                          'columns',
                                                                                                           'default': True},
                                                                                      'flatten_arrays': {'type': 'boolean',
                                                                                                         'description': 'Not '
                                                                                                                        'implemented: '
                                                                                                                        'accepted '
                                                                                                                        'and '
                                                                                                                        'stored, '
                                                                                                                        'but '
                                                                                                                        'reporting '
                                                                                                                        'keeps '
                                                                                                                        'each '
                                                                                                                        'array '
                                                                                                                        'in '
                                                                                                                        'one '
                                                                                                                        'JSONB '
                                                                                                                        'column, '
                                                                                                                        'one '
                                                                                                                        'row '
                                                                                                                        'per '
                                                                                                                        'document',
                                                                                                         'default': True},
                                                                                      'max_array_elements': {'type': 'integer',
                                                                                                             'maximum': 100.0,
                                                                                                             'minimum': 1.0,
                                                                                                             'description': 'Not '
                                                                                                                            'implemented '
                                                                                                                            '(see '
                                                                                                                            'flatten_arrays)',
                                                                                                             'default': 10},
                                                                                      'cross_version_view': {'additionalProperties': True,
                                                                                                             'type': 'object',
                                                                                                             'description': 'Opt-in '
                                                                                                                            'cross-version '
                                                                                                                            'entity '
                                                                                                                            'view '
                                                                                                                            'over '
                                                                                                                            'the '
                                                                                                                            'per-version '
                                                                                                                            'reporting '
                                                                                                                            'tables: '
                                                                                                                            "{'versions': "
                                                                                                                            "'all' "
                                                                                                                            '| '
                                                                                                                            '[ints], '
                                                                                                                            "'columns': "
                                                                                                                            '{target: '
                                                                                                                            "{'from': "
                                                                                                                            'source} '
                                                                                                                            '| '
                                                                                                                            '{}}}. '
                                                                                                                            'The '
                                                                                                                            'identity '
                                                                                                                            'core '
                                                                                                                            'is '
                                                                                                                            'always '
                                                                                                                            'included; '
                                                                                                                            'declared '
                                                                                                                            'column '
                                                                                                                            'mappings '
                                                                                                                            'extend '
                                                                                                                            'it. '
                                                                                                                            'Consumed '
                                                                                                                            'by '
                                                                                                                            'reporting-sync '
                                                                                                                            '(which '
                                                                                                                            'validates '
                                                                                                                            'the '
                                                                                                                            'shape); '
                                                                                                                            'stored '
                                                                                                                            'pass-through '
                                                                                                                            'here.'}},
                                                                       'type': 'object',
                                                                       'description': 'Configuration '
                                                                                      'for '
                                                                                      'PostgreSQL '
                                                                                      'reporting '
                                                                                      'sync'},
                                                         'created_by': {'type': 'string',
                                                                        'description': 'User or '
                                                                                       'system '
                                                                                       'creating '
                                                                                       'this '
                                                                                       'template'},
                                                         'status': {'type': 'string',
                                                                    'description': 'Initial '
                                                                                   'status: '
                                                                                   "'active' "
                                                                                   '(default) or '
                                                                                   "'draft' (skips "
                                                                                   'reference '
                                                                                   'validation)'}},
                                          'additionalProperties': False,
                                          'type': 'object',
                                          'required': ['value', 'label', 'namespace'],
                                          'description': 'Request to create a new template.'},
 'template-store#FieldDefinition': {'properties': {'name': {'type': 'string',
                                                            'description': 'Field name (used in '
                                                                           'data)',
                                                            'examples': ['first_name',
                                                                         'birth_date']},
                                                   'label': {'type': 'string',
                                                             'description': 'Human-readable label',
                                                             'examples': ['First Name',
                                                                          'Date of Birth']},
                                                   'type': {'type': 'string',
                                                            'enum': ['string',
                                                                     'number',
                                                                     'integer',
                                                                     'boolean',
                                                                     'date',
                                                                     'datetime',
                                                                     'term',
                                                                     'reference',
                                                                     'file',
                                                                     'object',
                                                                     'array'],
                                                            'description': 'Supported field types '
                                                                           'for template fields.'},
                                                   'mandatory': {'type': 'boolean',
                                                                 'description': 'Whether field is '
                                                                                'required',
                                                                 'default': False},
                                                   'default_value': {'description': 'A suggested '
                                                                                    'initial '
                                                                                    'value, for '
                                                                                    'clients (a '
                                                                                    "form's "
                                                                                    'prefill). '
                                                                                    'Informational '
                                                                                    'only: the '
                                                                                    'platform '
                                                                                    'never fills '
                                                                                    'an omitted '
                                                                                    'field with '
                                                                                    'it, so a '
                                                                                    'mandatory '
                                                                                    'field with a '
                                                                                    'default is '
                                                                                    'still '
                                                                                    'required on '
                                                                                    'every write.'},
                                                   'terminology_ref': {'type': 'string',
                                                                       'description': 'Terminology '
                                                                                      'for term '
                                                                                      'validation: '
                                                                                      'stored as '
                                                                                      'its '
                                                                                      'canonical '
                                                                                      'terminology_id '
                                                                                      'once the '
                                                                                      'template is '
                                                                                      'active (a '
                                                                                      'draft keeps '
                                                                                      'the '
                                                                                      'submitted '
                                                                                      'form until '
                                                                                      'activation)'},
                                                   'template_ref': {'type': 'string',
                                                                    'description': 'Template for '
                                                                                   'the nested '
                                                                                   'object: stored '
                                                                                   'as its '
                                                                                   'canonical '
                                                                                   'template_id '
                                                                                   'once the '
                                                                                   'template is '
                                                                                   'active (a '
                                                                                   'draft keeps '
                                                                                   'the submitted '
                                                                                   'form until '
                                                                                   'activation)'},
                                                   'template_ref_version': {'type': 'integer',
                                                                            'description': 'Pinned '
                                                                                           'version '
                                                                                           'of the '
                                                                                           'nested '
                                                                                           'template '
                                                                                           '(mandatory '
                                                                                           'when '
                                                                                           'template_ref '
                                                                                           'is '
                                                                                           'set)'},
                                                   'reference_type': {'type': 'string',
                                                                      'enum': ['document',
                                                                               'term',
                                                                               'terminology',
                                                                               'template'],
                                                                      'description': 'Type of '
                                                                                     'entity being '
                                                                                     'referenced '
                                                                                     '(for '
                                                                                     'reference '
                                                                                     'type)'},
                                                   'target_templates': {'items': {'type': 'string'},
                                                                        'type': 'array',
                                                                        'description': 'Allowed '
                                                                                       'document '
                                                                                       'reference '
                                                                                       'targets: '
                                                                                       'stored as '
                                                                                       'canonical '
                                                                                       'template_ids '
                                                                                       'once the '
                                                                                       'template '
                                                                                       'is active '
                                                                                       '(a draft '
                                                                                       'keeps the '
                                                                                       'submitted '
                                                                                       'forms '
                                                                                       'until '
                                                                                       'activation). '
                                                                                       'On an edge '
                                                                                       "type's "
                                                                                       'source_ref '
                                                                                       '/ '
                                                                                       'target_ref '
                                                                                       'this is '
                                                                                       'derived '
                                                                                       'from the '
                                                                                       'template-level '
                                                                                       'declaration '
                                                                                       'when '
                                                                                       'served, '
                                                                                       'and '
                                                                                       'discarded '
                                                                                       'on write'},
                                                   'include_subtypes': {'type': 'boolean',
                                                                        'description': 'When true, '
                                                                                       'target_templates '
                                                                                       'also '
                                                                                       'accepts '
                                                                                       'documents '
                                                                                       'from child '
                                                                                       'templates '
                                                                                       '(via '
                                                                                       'inheritance)'},
                                                   'target_terminologies': {'items': {'type': 'string'},
                                                                            'type': 'array',
                                                                            'description': 'Allowed '
                                                                                           'term '
                                                                                           'reference '
                                                                                           'targets: '
                                                                                           'stored '
                                                                                           'as '
                                                                                           'canonical '
                                                                                           'terminology_ids '
                                                                                           'once '
                                                                                           'the '
                                                                                           'template '
                                                                                           'is '
                                                                                           'active '
                                                                                           '(a '
                                                                                           'draft '
                                                                                           'keeps '
                                                                                           'the '
                                                                                           'submitted '
                                                                                           'forms '
                                                                                           'until '
                                                                                           'activation)'},
                                                   'version_strategy': {'type': 'string',
                                                                        'enum': ['latest',
                                                                                 'pinned'],
                                                                        'description': 'How to '
                                                                                       'resolve '
                                                                                       'reference '
                                                                                       'versions '
                                                                                       '(default: '
                                                                                       'latest)'},
                                                   'file_config': {'properties': {'allowed_types': {'items': {'type': 'string'},
                                                                                                    'type': 'array',
                                                                                                    'description': 'Allowed '
                                                                                                                   'MIME '
                                                                                                                   'type '
                                                                                                                   'patterns '
                                                                                                                   '(e.g., '
                                                                                                                   "'image/*', "
                                                                                                                   "'application/pdf'). "
                                                                                                                   'Must '
                                                                                                                   'not '
                                                                                                                   'be '
                                                                                                                   'empty; '
                                                                                                                   'omit '
                                                                                                                   'it '
                                                                                                                   'to '
                                                                                                                   'accept '
                                                                                                                   'every '
                                                                                                                   'type.',
                                                                                                    'default': ['*/*']},
                                                                                  'max_size_mb': {'type': 'number',
                                                                                                  'maximum': 100.0,
                                                                                                  'exclusiveMinimum': 0.0,
                                                                                                  'description': 'Maximum '
                                                                                                                 'file '
                                                                                                                 'size '
                                                                                                                 'in '
                                                                                                                 'MB '
                                                                                                                 '(max '
                                                                                                                 '100MB)',
                                                                                                  'default': 10.0},
                                                                                  'multiple': {'type': 'boolean',
                                                                                               'description': 'Allow '
                                                                                                              'multiple '
                                                                                                              'files '
                                                                                                              '(field '
                                                                                                              'value '
                                                                                                              'becomes '
                                                                                                              'array '
                                                                                                              'of '
                                                                                                              'file '
                                                                                                              'IDs)',
                                                                                               'default': False},
                                                                                  'max_files': {'type': 'integer',
                                                                                                'maximum': 100.0,
                                                                                                'minimum': 1.0,
                                                                                                'description': 'Maximum '
                                                                                                               'number '
                                                                                                               'of '
                                                                                                               'files '
                                                                                                               'when '
                                                                                                               'multiple=true '
                                                                                                               '(default: '
                                                                                                               'unlimited)'}},
                                                                   'type': 'object',
                                                                   'description': 'Configuration '
                                                                                  'for file fields '
                                                                                  '(allowed types, '
                                                                                  'size limits)'},
                                                   'array_item_type': {'type': 'string',
                                                                       'enum': ['string',
                                                                                'number',
                                                                                'integer',
                                                                                'boolean',
                                                                                'date',
                                                                                'datetime',
                                                                                'term',
                                                                                'reference',
                                                                                'file',
                                                                                'object',
                                                                                'array'],
                                                                       'description': 'Type of '
                                                                                      'array items '
                                                                                      '(for array '
                                                                                      'type)'},
                                                   'array_terminology_ref': {'type': 'string',
                                                                             'description': 'Terminology '
                                                                                            'for '
                                                                                            'array '
                                                                                            'item '
                                                                                            'term '
                                                                                            'validation: '
                                                                                            'stored '
                                                                                            'as '
                                                                                            'its '
                                                                                            'canonical '
                                                                                            'terminology_id '
                                                                                            'once '
                                                                                            'the '
                                                                                            'template '
                                                                                            'is '
                                                                                            'active '
                                                                                            '(a '
                                                                                            'draft '
                                                                                            'keeps '
                                                                                            'the '
                                                                                            'submitted '
                                                                                            'form '
                                                                                            'until '
                                                                                            'activation)'},
                                                   'array_template_ref': {'type': 'string',
                                                                          'description': 'Template '
                                                                                         'for '
                                                                                         'array '
                                                                                         'items: '
                                                                                         'stored '
                                                                                         'as its '
                                                                                         'canonical '
                                                                                         'template_id '
                                                                                         'once the '
                                                                                         'template '
                                                                                         'is '
                                                                                         'active '
                                                                                         '(a draft '
                                                                                         'keeps '
                                                                                         'the '
                                                                                         'submitted '
                                                                                         'form '
                                                                                         'until '
                                                                                         'activation)'},
                                                   'array_template_ref_version': {'type': 'integer',
                                                                                  'description': 'Pinned '
                                                                                                 'version '
                                                                                                 'of '
                                                                                                 'the '
                                                                                                 'array-item '
                                                                                                 'template '
                                                                                                 '(mandatory '
                                                                                                 'when '
                                                                                                 'array_template_ref '
                                                                                                 'is '
                                                                                                 'set)'},
                                                   'array_file_config': {'properties': {'allowed_types': {'items': {'type': 'string'},
                                                                                                          'type': 'array',
                                                                                                          'description': 'Allowed '
                                                                                                                         'MIME '
                                                                                                                         'type '
                                                                                                                         'patterns '
                                                                                                                         '(e.g., '
                                                                                                                         "'image/*', "
                                                                                                                         "'application/pdf'). "
                                                                                                                         'Must '
                                                                                                                         'not '
                                                                                                                         'be '
                                                                                                                         'empty; '
                                                                                                                         'omit '
                                                                                                                         'it '
                                                                                                                         'to '
                                                                                                                         'accept '
                                                                                                                         'every '
                                                                                                                         'type.',
                                                                                                          'default': ['*/*']},
                                                                                        'max_size_mb': {'type': 'number',
                                                                                                        'maximum': 100.0,
                                                                                                        'exclusiveMinimum': 0.0,
                                                                                                        'description': 'Maximum '
                                                                                                                       'file '
                                                                                                                       'size '
                                                                                                                       'in '
                                                                                                                       'MB '
                                                                                                                       '(max '
                                                                                                                       '100MB)',
                                                                                                        'default': 10.0},
                                                                                        'multiple': {'type': 'boolean',
                                                                                                     'description': 'Allow '
                                                                                                                    'multiple '
                                                                                                                    'files '
                                                                                                                    '(field '
                                                                                                                    'value '
                                                                                                                    'becomes '
                                                                                                                    'array '
                                                                                                                    'of '
                                                                                                                    'file '
                                                                                                                    'IDs)',
                                                                                                     'default': False},
                                                                                        'max_files': {'type': 'integer',
                                                                                                      'maximum': 100.0,
                                                                                                      'minimum': 1.0,
                                                                                                      'description': 'Maximum '
                                                                                                                     'number '
                                                                                                                     'of '
                                                                                                                     'files '
                                                                                                                     'when '
                                                                                                                     'multiple=true '
                                                                                                                     '(default: '
                                                                                                                     'unlimited)'}},
                                                                         'type': 'object',
                                                                         'description': 'File '
                                                                                        'configuration '
                                                                                        'for array '
                                                                                        'items if '
                                                                                        'file '
                                                                                        'type'},
                                                   'validation': {'properties': {'pattern': {'type': 'string',
                                                                                             'description': 'Regex '
                                                                                                            'pattern '
                                                                                                            'for '
                                                                                                            'string '
                                                                                                            'fields; '
                                                                                                            'must '
                                                                                                            'match '
                                                                                                            'the '
                                                                                                            'whole '
                                                                                                            'value '
                                                                                                            '(no '
                                                                                                            '^/$ '
                                                                                                            'anchors '
                                                                                                            'needed)'},
                                                                                 'min_length': {'type': 'integer',
                                                                                                'description': 'Minimum '
                                                                                                               'string '
                                                                                                               'length'},
                                                                                 'max_length': {'type': 'integer',
                                                                                                'description': 'Maximum '
                                                                                                               'string '
                                                                                                               'length'},
                                                                                 'minimum': {'type': 'number',
                                                                                             'description': 'Minimum '
                                                                                                            'numeric '
                                                                                                            'value'},
                                                                                 'maximum': {'type': 'number',
                                                                                             'description': 'Maximum '
                                                                                                            'numeric '
                                                                                                            'value'},
                                                                                 'enum': {'items': {},
                                                                                          'type': 'array',
                                                                                          'description': 'Allowed '
                                                                                                         'values '
                                                                                                         '(not '
                                                                                                         'term-based)'}},
                                                                  'type': 'object',
                                                                  'description': 'Field-level '
                                                                                 'validation '
                                                                                 'rules'},
                                                   'semantic_type': {'type': 'string',
                                                                     'enum': ['email',
                                                                              'url',
                                                                              'latitude',
                                                                              'longitude',
                                                                              'percentage',
                                                                              'duration',
                                                                              'geo_point'],
                                                                     'description': 'Semantic type '
                                                                                    'for '
                                                                                    'additional '
                                                                                    'validation '
                                                                                    '(email, url, '
                                                                                    'latitude, '
                                                                                    'etc.)'},
                                                   'full_text_indexed': {'type': 'boolean',
                                                                         'description': 'Enable '
                                                                                        'PostgreSQL '
                                                                                        'full-text '
                                                                                        'indexing '
                                                                                        'on this '
                                                                                        'field. '
                                                                                        'Only '
                                                                                        'valid for '
                                                                                        'type=string '
                                                                                        'and '
                                                                                        'requires '
                                                                                        'template '
                                                                                        'reporting.sync_enabled=true. '
                                                                                        'Future: '
                                                                                        'language '
                                                                                        'codes '
                                                                                        "('en', "
                                                                                        "'de') "
                                                                                        'will be '
                                                                                        'accepted; '
                                                                                        'v1 is '
                                                                                        'bool '
                                                                                        'only.'},
                                                   'inherited': {'type': 'boolean',
                                                                 'description': 'Whether this '
                                                                                'field is '
                                                                                'inherited from a '
                                                                                'parent template '
                                                                                '(set during '
                                                                                'resolution)'},
                                                   'inherited_from': {'type': 'string',
                                                                      'description': 'Template ID '
                                                                                     'of the '
                                                                                     'parent '
                                                                                     'template '
                                                                                     'this field '
                                                                                     'was '
                                                                                     'inherited '
                                                                                     'from'},
                                                   'metadata': {'additionalProperties': True,
                                                                'type': 'object',
                                                                'description': 'Additional field '
                                                                               'metadata'}},
                                    'type': 'object',
                                    'required': ['name', 'label', 'type'],
                                    'description': 'A field definition within a template.'}}


TOOL_PARAMS: dict[str, dict] = {'create_document': {'param_name': 'document',
                     'param_type': 'dict',
                     'schema_ref': 'document-store#DocumentCreateRequest'},
 'create_documents_bulk': {'param_name': 'documents',
                           'param_type': 'list',
                           'schema_ref': 'document-store#DocumentCreateRequest'},
 'create_template': {'param_name': 'template',
                     'param_type': 'dict',
                     'schema_ref': 'template-store#CreateTemplateRequest'},
 'create_templates_bulk': {'param_name': 'templates',
                           'param_type': 'list',
                           'schema_ref': 'template-store#CreateTemplateRequest'},
 'create_term_relations': {'param_name': 'term_relations',
                           'param_type': 'list',
                           'schema_ref': 'def-store#CreateTermRelationRequest'},
 'create_terminologies_bulk': {'param_name': 'items',
                               'param_type': 'list',
                               'schema_ref': 'def-store#CreateTerminologyRequest'},
 'create_terms': {'param_name': 'terms',
                  'param_type': 'list',
                  'schema_ref': 'def-store#CreateTermRequest'}}


TOOL_FIELD_DOCS: dict[str, str] = {
    'create_document': 'Fields (from OpenAPI — these are the exact field names):\ntemplate_id (string, REQUIRED): Template ID to validate against\ntemplate_version (integer): Specific template version to validate against (default: latest)\ndocument_id (string): With version: the pre-assigned document ID of a restore or migration, which the Registry uses as-is. Without version: the document this write updates — it must be the one the identity resolves to, or the item is refused (invalid_request, details.rule document_id_mismatch).\nversion (integer): Pre-assigned version (for restore/migration — skips Registry and version computation when used with document_id)\nnamespace (string, REQUIRED): Namespace for the document\ndata (object, REQUIRED): Document content\ncreated_by (string): User or system creating this document\nmetadata (object): Custom metadata\nsynonyms (array of object): Optional synonym composite keys to register for this document in the Registry\non_synonym_conflict (enum, default: "fail"): One of: fail, warn. How to handle an inline synonym that the Registry refuses (e.g. already owned by a different entry). \'fail\' (default): the whole create fails, the document is NOT created, and any synonyms/entry allocated for it are rolled back — use this when the synonyms are load-bearing (e.g. you rely on original IDs to resolve after import). \'warn\': the document is created and the refused synonyms are reported in `warnings`.',
    'create_documents_bulk': 'Fields (from OpenAPI — these are the exact field names):\ntemplate_id (string, REQUIRED): Template ID to validate against\ntemplate_version (integer): Specific template version to validate against (default: latest)\ndocument_id (string): With version: the pre-assigned document ID of a restore or migration, which the Registry uses as-is. Without version: the document this write updates — it must be the one the identity resolves to, or the item is refused (invalid_request, details.rule document_id_mismatch).\nversion (integer): Pre-assigned version (for restore/migration — skips Registry and version computation when used with document_id)\nnamespace (string, REQUIRED): Namespace for the document\ndata (object, REQUIRED): Document content\ncreated_by (string): User or system creating this document\nmetadata (object): Custom metadata\nsynonyms (array of object): Optional synonym composite keys to register for this document in the Registry\non_synonym_conflict (enum, default: "fail"): One of: fail, warn. How to handle an inline synonym that the Registry refuses (e.g. already owned by a different entry). \'fail\' (default): the whole create fails, the document is NOT created, and any synonyms/entry allocated for it are rolled back — use this when the synonyms are load-bearing (e.g. you rely on original IDs to resolve after import). \'warn\': the document is created and the refused synonyms are reported in `warnings`.',
    'create_template': 'Fields (from OpenAPI — these are the exact field names):\nvalue (string, REQUIRED): Human-readable value (e.g., \'PERSON\')\nlabel (string, REQUIRED): Display label\ndescription (string): Detailed description\ntemplate_id (string): Pre-assigned template ID (for restore/migration — Registry uses as-is instead of generating)\nversion (integer): Pre-assigned version (for restore/migration, with template_id): skips the existing-value check and version computation; the identity is still registered with the Registry\nnamespace (string, REQUIRED): Namespace for the template\nextends (string): Parent template ID for inheritance\nextends_version (integer): Pinned parent version. Required (non-null) whenever the template declares \'extends\' — a null there is rejected; the parent is validated against this exact pinned version, never \'latest\'. Null only when there is no parent.\nidentity_fields (array of string): Fields that form the composite identity key\nheader_fields (array of string): Fields to include in peer/header projections. Bare names → data.<name>; metadata.custom.<name> paths allowed. Empty → projection falls back to identity_fields.\nrenames (object): Field renames relative to the previous version, {new_field: old_field}. Only meaningful when this create versions an existing template (create is an upsert); rejected on a first version. Identity fields cannot be renamed. Declared renames migrate losslessly and map in reporting.\nusage (enum): One of: entity, reference, relationship. How a template\'s documents are intended to be used.\n\n- entity (default): full document lifecycle, the v1.x behaviour.\n- reference: lightweight controlled-vocabulary documents (LOV).\n  Reserved for a future phase; currently behaves like entity.\n- relationship: typed, property-carrying edge between two\n  documents. Requires source_templates / target_templates to be set\n  and a source_ref / target_ref reference field on the template.\n  See docs/design/document-relationships.md.\nsource_templates (array of object): Templates allowed as edge source (required when usage=relationship; rejected with an error on non-relationship templates). Each entry is a template value, a canonical template_id, ns:VALUE, or a full {lookup_value, resolved} entry; stored and returned as two-half entries whose resolved half the platform fills\ntarget_templates (array of object): Templates allowed as edge target (required when usage=relationship; rejected with an error on non-relationship templates). Each entry is a template value, a canonical template_id, ns:VALUE, or a full {lookup_value, resolved} entry; stored and returned as two-half entries whose resolved half the platform fills\nversioned (boolean, default: true): True = updates create new versions; False = overwrite in place. Immutable after creation.\nfields (array of object): Field definitions\n  Each item:\n    name (string, REQUIRED): Field name (used in data)\n    label (string, REQUIRED): Human-readable label\n    type (enum, REQUIRED): One of: string, number, integer, boolean, date, datetime, term, reference, file, object, array. Supported field types for template fields.\n    mandatory (boolean, default: false): Whether field is required\n    default_value (object): A suggested initial value, for clients (a form\'s prefill). Informational only: the platform never fills an omitted field with it, so a mandatory field with a default is still required on every write.\n    terminology_ref (string): Terminology for term validation: stored as its canonical terminology_id once the template is active (a draft keeps the submitted form until activation)\n    template_ref (string): Template for the nested object: stored as its canonical template_id once the template is active (a draft keeps the submitted form until activation)\n    template_ref_version (integer): Pinned version of the nested template (mandatory when template_ref is set)\n    reference_type (enum): One of: document, term, terminology, template. Type of entity being referenced (for reference type)\n    target_templates (array of string): Allowed document reference targets: stored as canonical template_ids once the template is active (a draft keeps the submitted forms until activation). On an edge type\'s source_ref / target_ref this is derived from the template-level declaration when served, and discarded on write\n    include_subtypes (boolean): When true, target_templates also accepts documents from child templates (via inheritance)\n    target_terminologies (array of string): Allowed term reference targets: stored as canonical terminology_ids once the template is active (a draft keeps the submitted forms until activation)\n    version_strategy (enum): One of: latest, pinned. How to resolve reference versions (default: latest)\n    file_config (object): Configuration for file fields (allowed types, size limits)\n      (nested object — see full schema)\n    array_item_type (enum): One of: string, number, integer, boolean, date, datetime, term, reference, file, object, array. Type of array items (for array type)\n    array_terminology_ref (string): Terminology for array item term validation: stored as its canonical terminology_id once the template is active (a draft keeps the submitted form until activation)\n    array_template_ref (string): Template for array items: stored as its canonical template_id once the template is active (a draft keeps the submitted form until activation)\n    array_template_ref_version (integer): Pinned version of the array-item template (mandatory when array_template_ref is set)\n    array_file_config (object): File configuration for array items if file type\n      (nested object — see full schema)\n    validation (object): Field-level validation rules\n      (nested object — see full schema)\n    semantic_type (enum): One of: email, url, latitude, longitude, percentage, duration, geo_point. Semantic type for additional validation (email, url, latitude, etc.)\n    full_text_indexed (boolean): Enable PostgreSQL full-text indexing on this field. Only valid for type=string and requires template reporting.sync_enabled=true. Future: language codes (\'en\', \'de\') will be accepted; v1 is bool only.\n    inherited (boolean): Whether this field is inherited from a parent template (set during resolution)\n    inherited_from (string): Template ID of the parent template this field was inherited from\n    metadata (object): Additional field metadata\nrules (array of object): Cross-field validation rules\n  Each item:\n    type (enum, REQUIRED): One of: conditional_required, conditional_value, mutual_exclusion, dependency, pattern, range. Types of cross-field validation rules.\n    description (string): Human-readable description of the rule\n    conditions (array of object): Conditions that trigger the rule\n      Each item:\n        (nested object — see full schema)\n    target_field (string): Field affected by the rule\n    target_fields (array of string): Fields affected (for mutual_exclusion)\n    required (boolean): For conditional_required: is field required?\n    allowed_values (array of object): For conditional_value: allowed values\n    pattern (string): For pattern: regex pattern\n    minimum (number): For range: minimum value\n    maximum (number): For range: maximum value\n    error_message (string): Custom error message\nmetadata (object): Additional metadata\n  domain (string): Business domain (e.g., \'hr\', \'finance\', \'healthcare\')\n  category (string): Template category (e.g., \'master_data\', \'transaction\')\n  tags (array of string): Tags for categorization and search\n  custom (object): Custom metadata fields\nreporting (object): Configuration for PostgreSQL reporting sync\n  sync_enabled (boolean, default: true): Whether to sync documents of this template to PostgreSQL\n  sync_strategy (string, default: "latest_only"): Sync strategy: \'latest_only\' (upsert the latest version), \'all_versions\' (insert every version) or \'disabled\'\n  table_name (string): Custom PostgreSQL table name base (doc_<value> if not set). Fixed once the template exists: its reporting tables are named from it\n  include_metadata (boolean, default: true): Include created_at, created_by, etc. columns\n  flatten_arrays (boolean, default: true): Not implemented: accepted and stored, but reporting keeps each array in one JSONB column, one row per document\n  max_array_elements (integer, default: 10): Not implemented (see flatten_arrays)\n  cross_version_view (object): Opt-in cross-version entity view over the per-version reporting tables: {\'versions\': \'all\' | [ints], \'columns\': {target: {\'from\': source} | {}}}. The identity core is always included; declared column mappings extend it. Consumed by reporting-sync (which validates the shape); stored pass-through here.\ncreated_by (string): User or system creating this template\nstatus (string): Initial status: \'active\' (default) or \'draft\' (skips reference validation)\n\nFieldDefinition fields:\n  name (string, REQUIRED): Field name (used in data)\n  label (string, REQUIRED): Human-readable label\n  type (enum, REQUIRED): One of: string, number, integer, boolean, date, datetime, term, reference, file, object, array. Supported field types for template fields.\n  mandatory (boolean, default: false): Whether field is required\n  default_value (object): A suggested initial value, for clients (a form\'s prefill). Informational only: the platform never fills an omitted field with it, so a mandatory field with a default is still required on every write.\n  terminology_ref (string): Terminology for term validation: stored as its canonical terminology_id once the template is active (a draft keeps the submitted form until activation)\n  template_ref (string): Template for the nested object: stored as its canonical template_id once the template is active (a draft keeps the submitted form until activation)\n  template_ref_version (integer): Pinned version of the nested template (mandatory when template_ref is set)\n  reference_type (enum): One of: document, term, terminology, template. Type of entity being referenced (for reference type)\n  target_templates (array of string): Allowed document reference targets: stored as canonical template_ids once the template is active (a draft keeps the submitted forms until activation). On an edge type\'s source_ref / target_ref this is derived from the template-level declaration when served, and discarded on write\n  include_subtypes (boolean): When true, target_templates also accepts documents from child templates (via inheritance)\n  target_terminologies (array of string): Allowed term reference targets: stored as canonical terminology_ids once the template is active (a draft keeps the submitted forms until activation)\n  version_strategy (enum): One of: latest, pinned. How to resolve reference versions (default: latest)\n  file_config (object): Configuration for file fields (allowed types, size limits)\n    (nested object — see full schema)\n  array_item_type (enum): One of: string, number, integer, boolean, date, datetime, term, reference, file, object, array. Type of array items (for array type)\n  array_terminology_ref (string): Terminology for array item term validation: stored as its canonical terminology_id once the template is active (a draft keeps the submitted form until activation)\n  array_template_ref (string): Template for array items: stored as its canonical template_id once the template is active (a draft keeps the submitted form until activation)\n  array_template_ref_version (integer): Pinned version of the array-item template (mandatory when array_template_ref is set)\n  array_file_config (object): File configuration for array items if file type\n    (nested object — see full schema)\n  validation (object): Field-level validation rules\n    (nested object — see full schema)\n  semantic_type (enum): One of: email, url, latitude, longitude, percentage, duration, geo_point. Semantic type for additional validation (email, url, latitude, etc.)\n  full_text_indexed (boolean): Enable PostgreSQL full-text indexing on this field. Only valid for type=string and requires template reporting.sync_enabled=true. Future: language codes (\'en\', \'de\') will be accepted; v1 is bool only.\n  inherited (boolean): Whether this field is inherited from a parent template (set during resolution)\n  inherited_from (string): Template ID of the parent template this field was inherited from\n  metadata (object): Additional field metadata',
    'create_templates_bulk': 'Fields (from OpenAPI — these are the exact field names):\nvalue (string, REQUIRED): Human-readable value (e.g., \'PERSON\')\nlabel (string, REQUIRED): Display label\ndescription (string): Detailed description\ntemplate_id (string): Pre-assigned template ID (for restore/migration — Registry uses as-is instead of generating)\nversion (integer): Pre-assigned version (for restore/migration, with template_id): skips the existing-value check and version computation; the identity is still registered with the Registry\nnamespace (string, REQUIRED): Namespace for the template\nextends (string): Parent template ID for inheritance\nextends_version (integer): Pinned parent version. Required (non-null) whenever the template declares \'extends\' — a null there is rejected; the parent is validated against this exact pinned version, never \'latest\'. Null only when there is no parent.\nidentity_fields (array of string): Fields that form the composite identity key\nheader_fields (array of string): Fields to include in peer/header projections. Bare names → data.<name>; metadata.custom.<name> paths allowed. Empty → projection falls back to identity_fields.\nrenames (object): Field renames relative to the previous version, {new_field: old_field}. Only meaningful when this create versions an existing template (create is an upsert); rejected on a first version. Identity fields cannot be renamed. Declared renames migrate losslessly and map in reporting.\nusage (enum): One of: entity, reference, relationship. How a template\'s documents are intended to be used.\n\n- entity (default): full document lifecycle, the v1.x behaviour.\n- reference: lightweight controlled-vocabulary documents (LOV).\n  Reserved for a future phase; currently behaves like entity.\n- relationship: typed, property-carrying edge between two\n  documents. Requires source_templates / target_templates to be set\n  and a source_ref / target_ref reference field on the template.\n  See docs/design/document-relationships.md.\nsource_templates (array of object): Templates allowed as edge source (required when usage=relationship; rejected with an error on non-relationship templates). Each entry is a template value, a canonical template_id, ns:VALUE, or a full {lookup_value, resolved} entry; stored and returned as two-half entries whose resolved half the platform fills\ntarget_templates (array of object): Templates allowed as edge target (required when usage=relationship; rejected with an error on non-relationship templates). Each entry is a template value, a canonical template_id, ns:VALUE, or a full {lookup_value, resolved} entry; stored and returned as two-half entries whose resolved half the platform fills\nversioned (boolean, default: true): True = updates create new versions; False = overwrite in place. Immutable after creation.\nfields (array of object): Field definitions\n  Each item:\n    name (string, REQUIRED): Field name (used in data)\n    label (string, REQUIRED): Human-readable label\n    type (enum, REQUIRED): One of: string, number, integer, boolean, date, datetime, term, reference, file, object, array. Supported field types for template fields.\n    mandatory (boolean, default: false): Whether field is required\n    default_value (object): A suggested initial value, for clients (a form\'s prefill). Informational only: the platform never fills an omitted field with it, so a mandatory field with a default is still required on every write.\n    terminology_ref (string): Terminology for term validation: stored as its canonical terminology_id once the template is active (a draft keeps the submitted form until activation)\n    template_ref (string): Template for the nested object: stored as its canonical template_id once the template is active (a draft keeps the submitted form until activation)\n    template_ref_version (integer): Pinned version of the nested template (mandatory when template_ref is set)\n    reference_type (enum): One of: document, term, terminology, template. Type of entity being referenced (for reference type)\n    target_templates (array of string): Allowed document reference targets: stored as canonical template_ids once the template is active (a draft keeps the submitted forms until activation). On an edge type\'s source_ref / target_ref this is derived from the template-level declaration when served, and discarded on write\n    include_subtypes (boolean): When true, target_templates also accepts documents from child templates (via inheritance)\n    target_terminologies (array of string): Allowed term reference targets: stored as canonical terminology_ids once the template is active (a draft keeps the submitted forms until activation)\n    version_strategy (enum): One of: latest, pinned. How to resolve reference versions (default: latest)\n    file_config (object): Configuration for file fields (allowed types, size limits)\n      (nested object — see full schema)\n    array_item_type (enum): One of: string, number, integer, boolean, date, datetime, term, reference, file, object, array. Type of array items (for array type)\n    array_terminology_ref (string): Terminology for array item term validation: stored as its canonical terminology_id once the template is active (a draft keeps the submitted form until activation)\n    array_template_ref (string): Template for array items: stored as its canonical template_id once the template is active (a draft keeps the submitted form until activation)\n    array_template_ref_version (integer): Pinned version of the array-item template (mandatory when array_template_ref is set)\n    array_file_config (object): File configuration for array items if file type\n      (nested object — see full schema)\n    validation (object): Field-level validation rules\n      (nested object — see full schema)\n    semantic_type (enum): One of: email, url, latitude, longitude, percentage, duration, geo_point. Semantic type for additional validation (email, url, latitude, etc.)\n    full_text_indexed (boolean): Enable PostgreSQL full-text indexing on this field. Only valid for type=string and requires template reporting.sync_enabled=true. Future: language codes (\'en\', \'de\') will be accepted; v1 is bool only.\n    inherited (boolean): Whether this field is inherited from a parent template (set during resolution)\n    inherited_from (string): Template ID of the parent template this field was inherited from\n    metadata (object): Additional field metadata\nrules (array of object): Cross-field validation rules\n  Each item:\n    type (enum, REQUIRED): One of: conditional_required, conditional_value, mutual_exclusion, dependency, pattern, range. Types of cross-field validation rules.\n    description (string): Human-readable description of the rule\n    conditions (array of object): Conditions that trigger the rule\n      Each item:\n        (nested object — see full schema)\n    target_field (string): Field affected by the rule\n    target_fields (array of string): Fields affected (for mutual_exclusion)\n    required (boolean): For conditional_required: is field required?\n    allowed_values (array of object): For conditional_value: allowed values\n    pattern (string): For pattern: regex pattern\n    minimum (number): For range: minimum value\n    maximum (number): For range: maximum value\n    error_message (string): Custom error message\nmetadata (object): Additional metadata\n  domain (string): Business domain (e.g., \'hr\', \'finance\', \'healthcare\')\n  category (string): Template category (e.g., \'master_data\', \'transaction\')\n  tags (array of string): Tags for categorization and search\n  custom (object): Custom metadata fields\nreporting (object): Configuration for PostgreSQL reporting sync\n  sync_enabled (boolean, default: true): Whether to sync documents of this template to PostgreSQL\n  sync_strategy (string, default: "latest_only"): Sync strategy: \'latest_only\' (upsert the latest version), \'all_versions\' (insert every version) or \'disabled\'\n  table_name (string): Custom PostgreSQL table name base (doc_<value> if not set). Fixed once the template exists: its reporting tables are named from it\n  include_metadata (boolean, default: true): Include created_at, created_by, etc. columns\n  flatten_arrays (boolean, default: true): Not implemented: accepted and stored, but reporting keeps each array in one JSONB column, one row per document\n  max_array_elements (integer, default: 10): Not implemented (see flatten_arrays)\n  cross_version_view (object): Opt-in cross-version entity view over the per-version reporting tables: {\'versions\': \'all\' | [ints], \'columns\': {target: {\'from\': source} | {}}}. The identity core is always included; declared column mappings extend it. Consumed by reporting-sync (which validates the shape); stored pass-through here.\ncreated_by (string): User or system creating this template\nstatus (string): Initial status: \'active\' (default) or \'draft\' (skips reference validation)\n\nFieldDefinition fields:\n  name (string, REQUIRED): Field name (used in data)\n  label (string, REQUIRED): Human-readable label\n  type (enum, REQUIRED): One of: string, number, integer, boolean, date, datetime, term, reference, file, object, array. Supported field types for template fields.\n  mandatory (boolean, default: false): Whether field is required\n  default_value (object): A suggested initial value, for clients (a form\'s prefill). Informational only: the platform never fills an omitted field with it, so a mandatory field with a default is still required on every write.\n  terminology_ref (string): Terminology for term validation: stored as its canonical terminology_id once the template is active (a draft keeps the submitted form until activation)\n  template_ref (string): Template for the nested object: stored as its canonical template_id once the template is active (a draft keeps the submitted form until activation)\n  template_ref_version (integer): Pinned version of the nested template (mandatory when template_ref is set)\n  reference_type (enum): One of: document, term, terminology, template. Type of entity being referenced (for reference type)\n  target_templates (array of string): Allowed document reference targets: stored as canonical template_ids once the template is active (a draft keeps the submitted forms until activation). On an edge type\'s source_ref / target_ref this is derived from the template-level declaration when served, and discarded on write\n  include_subtypes (boolean): When true, target_templates also accepts documents from child templates (via inheritance)\n  target_terminologies (array of string): Allowed term reference targets: stored as canonical terminology_ids once the template is active (a draft keeps the submitted forms until activation)\n  version_strategy (enum): One of: latest, pinned. How to resolve reference versions (default: latest)\n  file_config (object): Configuration for file fields (allowed types, size limits)\n    (nested object — see full schema)\n  array_item_type (enum): One of: string, number, integer, boolean, date, datetime, term, reference, file, object, array. Type of array items (for array type)\n  array_terminology_ref (string): Terminology for array item term validation: stored as its canonical terminology_id once the template is active (a draft keeps the submitted form until activation)\n  array_template_ref (string): Template for array items: stored as its canonical template_id once the template is active (a draft keeps the submitted form until activation)\n  array_template_ref_version (integer): Pinned version of the array-item template (mandatory when array_template_ref is set)\n  array_file_config (object): File configuration for array items if file type\n    (nested object — see full schema)\n  validation (object): Field-level validation rules\n    (nested object — see full schema)\n  semantic_type (enum): One of: email, url, latitude, longitude, percentage, duration, geo_point. Semantic type for additional validation (email, url, latitude, etc.)\n  full_text_indexed (boolean): Enable PostgreSQL full-text indexing on this field. Only valid for type=string and requires template reporting.sync_enabled=true. Future: language codes (\'en\', \'de\') will be accepted; v1 is bool only.\n  inherited (boolean): Whether this field is inherited from a parent template (set during resolution)\n  inherited_from (string): Template ID of the parent template this field was inherited from\n  metadata (object): Additional field metadata',
    'create_term_relations': "Fields (from OpenAPI — these are the exact field names):\nsource_term_id (string, REQUIRED): The subject term: canonical UUID, fully qualified 'ns:terminology:value', or — with source_terminology set — the OPAQUE raw term value (never colon-parsed). The ambiguous 2-part 'TERMINOLOGY:VALUE' shorthand is rejected.\ntarget_term_id (string, REQUIRED): The object term; same accepted forms as source_term_id, scoped by target_terminology.\nsource_terminology (string): Terminology scoping a value-form source_term_id. When set, source_term_id is treated as the raw value — required for values that themselves contain ':' (OBO ids like GO:0000278). Per-item because a relation's two endpoints may live in different terminologies.\ntarget_terminology (string): Terminology scoping a value-form target_term_id (see source_terminology).\nrelation_type (string, REQUIRED): Relation type value (e.g., 'is_a', 'part_of')\nmetadata (object): Provenance, confidence, OWL axioms\ncreated_by (string): User or system creating this relation",
    'create_terminologies_bulk': 'Fields (from OpenAPI — these are the exact field names):\nvalue (string, REQUIRED): Human-readable value (e.g., \'DOC_STATUS\')\nlabel (string, REQUIRED): Display label\ndescription (string): Detailed description\nterminology_id (string): Pre-assigned terminology ID (for restore/migration — Registry uses as-is instead of generating)\nnamespace (string, REQUIRED): Namespace for the terminology\ncase_sensitive (boolean, default: false): Whether term values are case-sensitive\nallow_multiple (boolean, default: false): Whether multiple terms can be selected\nmutable (boolean, default: false): Whether terms can be hard-deleted (vs deprecated)\nunique_names (boolean, default: false): Reject (not just warn about) term writes whose names collide with another term\'s value or name\nmetadata (object): Additional metadata\n  source (string): Source of the terminology (e.g., \'ISO 3166\', \'internal\')\n  source_url (string): URL to the source specification\n  version (string): Version of the terminology (e.g., \'2024.1\')\n  language (string, default: "en"): Primary language code (ISO 639-1)\n  custom (object): Custom metadata fields\ncreated_by (string): Ignored: created_by is the authenticated caller. Accepted for compatibility',
    'create_terminology': 'Fields (from OpenAPI — these are the exact field names):\nvalue (string, REQUIRED): Human-readable value (e.g., \'DOC_STATUS\')\nlabel (string, REQUIRED): Display label\ndescription (string): Detailed description\nterminology_id (string): Pre-assigned terminology ID (for restore/migration — Registry uses as-is instead of generating)\nnamespace (string, REQUIRED): Namespace for the terminology\ncase_sensitive (boolean, default: false): Whether term values are case-sensitive\nallow_multiple (boolean, default: false): Whether multiple terms can be selected\nmutable (boolean, default: false): Whether terms can be hard-deleted (vs deprecated)\nunique_names (boolean, default: false): Reject (not just warn about) term writes whose names collide with another term\'s value or name\nmetadata (object): Additional metadata\n  source (string): Source of the terminology (e.g., \'ISO 3166\', \'internal\')\n  source_url (string): URL to the source specification\n  version (string): Version of the terminology (e.g., \'2024.1\')\n  language (string, default: "en"): Primary language code (ISO 639-1)\n  custom (object): Custom metadata fields\ncreated_by (string): Ignored: created_by is the authenticated caller. Accepted for compatibility',
    'create_terms': "Fields (from OpenAPI — these are the exact field names):\nvalue (string, REQUIRED): The value stored in documents (unique within terminology)\nterm_id (string): Pre-assigned term ID (for restore/migration — Registry uses as-is instead of generating)\nnames (array of object): Alternative names that resolve to this term, each with optional type, language, source and preferred flag\n  Each item:\n    value (string, REQUIRED): The name itself (e.g., 'Avastin', 'bevacizumab', 'NSC-704865')\n    type (string): Name type from the _NAME_TYPES terminology (e.g., 'INN', 'TRADE_GLOBAL', 'CODE'). None = untyped synonym.\n    language (string): Language code (ISO 639-1) for language-specific names\n    description (string): Free-text note on this name (e.g., a translated description)\n    source (string): Where this name came from (e.g., 'RTiS', 'OBO:hasExactSynonym')\n    preferred (boolean, default: false): Preferred name within its (type, language) group — at most one per group\naliases (array of string): Legacy shape: plain alternative values. Normalized into untyped names on write.\nlabel (string): Display label for UI. Defaults to value if not provided.\ndescription (string): Detailed description\nsort_order (integer, default: 0): Sort order within terminology\nparent_term_id (string): Parent term for hierarchical terms: an active term of the same terminology, by id or (on this door) by its value\ntranslations (array of object): Legacy shape: per-language labels. Normalized into language-bearing names on write.\n  Each item:\n    language (string, REQUIRED): Language code (ISO 639-1)\n    label (string, REQUIRED): Translated display label\n    description (string): Translated description\nmetadata (object): Custom metadata\ncreated_by (string): User or system creating this term",
}
