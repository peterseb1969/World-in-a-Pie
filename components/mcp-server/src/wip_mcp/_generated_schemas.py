"""Auto-generated from WIP OpenAPI specs. Do not edit manually.

Regenerate with: python -m scripts.generate_schemas [--fetch]
"""


TOOL_SCHEMAS: dict[str, dict] = {'def-store#CreateRelationshipRequest': {'properties': {'source_term_id': {'type': 'string',
                                                                           'description': 'The '
                                                                                          'subject '
                                                                                          'term '
                                                                                          'ID'},
                                                        'target_term_id': {'type': 'string',
                                                                           'description': 'The '
                                                                                          'object '
                                                                                          'term '
                                                                                          'ID'},
                                                        'relationship_type': {'type': 'string',
                                                                              'description': 'Relationship '
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
                                                                                      'relationship'}},
                                         'additionalProperties': False,
                                         'type': 'object',
                                         'required': ['source_term_id',
                                                      'target_term_id',
                                                      'relationship_type'],
                                         'description': 'Request to create a typed relationship '
                                                        'between two terms.'},
 'def-store#CreateTermRequest': {'properties': {'value': {'type': 'string',
                                                          'description': 'The value stored in '
                                                                         'documents (unique within '
                                                                         'terminology)'},
                                                'aliases': {'items': {'type': 'string'},
                                                            'type': 'array',
                                                            'description': 'Alternative values '
                                                                           'that resolve to this '
                                                                           "term (e.g., ['MR.', "
                                                                           "'mr'])"},
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
                                                                   'description': 'Parent term ID '
                                                                                  'for '
                                                                                  'hierarchical '
                                                                                  'terms'},
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
                                                                           'description': 'Translation '
                                                                                          'of a '
                                                                                          "term's "
                                                                                          'display '
                                                                                          'values.'},
                                                                 'type': 'array',
                                                                 'description': 'Translations'},
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
                                                       'extensible': {'type': 'boolean',
                                                                      'description': 'Whether '
                                                                                     'users can '
                                                                                     'add new '
                                                                                     'terms at '
                                                                                     'runtime',
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
                                                                      'description': 'User or '
                                                                                     'system '
                                                                                     'creating '
                                                                                     'this '
                                                                                     'terminology'}},
                                        'additionalProperties': False,
                                        'type': 'object',
                                        'required': ['value', 'label'],
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
                                                                         'description': 'Pre-assigned '
                                                                                        'document '
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
                                                                                     'Registry'}},
                                          'additionalProperties': False,
                                          'type': 'object',
                                          'required': ['template_id', 'data'],
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
                                                                                    'restore/migration '
                                                                                    '— skips '
                                                                                    'Registry and '
                                                                                    'version '
                                                                                    'computation '
                                                                                    'when used '
                                                                                    'with '
                                                                                    'template_id)'},
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
                                                                                            'version '
                                                                                            '(None '
                                                                                            '= '
                                                                                            'always '
                                                                                            'use '
                                                                                            'latest '
                                                                                            'active '
                                                                                            'parent '
                                                                                            'version)'},
                                                         'identity_fields': {'items': {'type': 'string'},
                                                                             'type': 'array',
                                                                             'description': 'Fields '
                                                                                            'that '
                                                                                            'form '
                                                                                            'the '
                                                                                            'composite '
                                                                                            'identity '
                                                                                            'key'},
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
                                                                                             'default_value': {'description': 'Default '
                                                                                                                              'value '
                                                                                                                              'if '
                                                                                                                              'not '
                                                                                                                              'provided'},
                                                                                             'terminology_ref': {'type': 'string',
                                                                                                                 'description': 'Canonical '
                                                                                                                                'terminology_id '
                                                                                                                                'for '
                                                                                                                                'term '
                                                                                                                                'validation '
                                                                                                                                '(resolved '
                                                                                                                                'from '
                                                                                                                                'value '
                                                                                                                                'at '
                                                                                                                                'creation)'},
                                                                                             'template_ref': {'type': 'string',
                                                                                                              'description': 'Canonical '
                                                                                                                             'template_id '
                                                                                                                             'for '
                                                                                                                             'nested '
                                                                                                                             'template '
                                                                                                                             '(resolved '
                                                                                                                             'from '
                                                                                                                             'value '
                                                                                                                             'at '
                                                                                                                             'creation)'},
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
                                                                                                                  'description': 'Canonical '
                                                                                                                                 'template_ids '
                                                                                                                                 'for '
                                                                                                                                 'allowed '
                                                                                                                                 'document '
                                                                                                                                 'reference '
                                                                                                                                 'targets '
                                                                                                                                 '(resolved '
                                                                                                                                 'from '
                                                                                                                                 'values '
                                                                                                                                 'at '
                                                                                                                                 'creation)'},
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
                                                                                                                      'description': 'Canonical '
                                                                                                                                     'terminology_ids '
                                                                                                                                     'for '
                                                                                                                                     'allowed '
                                                                                                                                     'term '
                                                                                                                                     'reference '
                                                                                                                                     'targets '
                                                                                                                                     '(resolved '
                                                                                                                                     'from '
                                                                                                                                     'values '
                                                                                                                                     'at '
                                                                                                                                     'creation)'},
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
                                                                                                                                                             "'application/pdf')",
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
                                                                                                                       'description': 'Canonical '
                                                                                                                                      'terminology_id '
                                                                                                                                      'for '
                                                                                                                                      'array '
                                                                                                                                      'item '
                                                                                                                                      'term '
                                                                                                                                      'validation '
                                                                                                                                      '(resolved '
                                                                                                                                      'from '
                                                                                                                                      'value '
                                                                                                                                      'at '
                                                                                                                                      'creation)'},
                                                                                             'array_template_ref': {'type': 'string',
                                                                                                                    'description': 'Canonical '
                                                                                                                                   'template_id '
                                                                                                                                   'for '
                                                                                                                                   'array '
                                                                                                                                   'item '
                                                                                                                                   'template '
                                                                                                                                   '(resolved '
                                                                                                                                   'from '
                                                                                                                                   'value '
                                                                                                                                   'at '
                                                                                                                                   'creation)'},
                                                                                             'array_file_config': {'properties': {'allowed_types': {'items': {'type': 'string'},
                                                                                                                                                    'type': 'array',
                                                                                                                                                    'description': 'Allowed '
                                                                                                                                                                   'MIME '
                                                                                                                                                                   'type '
                                                                                                                                                                   'patterns '
                                                                                                                                                                   '(e.g., '
                                                                                                                                                                   "'image/*', "
                                                                                                                                                                   "'application/pdf')",
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
                                                                                                                                                      'fields'},
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
                                                                                                                       '(upsert) '
                                                                                                                       'or '
                                                                                                                       "'all_versions' "
                                                                                                                       '(insert '
                                                                                                                       'all)',
                                                                                                        'default': 'latest_only'},
                                                                                      'table_name': {'type': 'string',
                                                                                                     'description': 'Custom '
                                                                                                                    'PostgreSQL '
                                                                                                                    'table '
                                                                                                                    'name '
                                                                                                                    '(auto-generated '
                                                                                                                    'from '
                                                                                                                    'value '
                                                                                                                    'if '
                                                                                                                    'not '
                                                                                                                    'set)'},
                                                                                      'include_metadata': {'type': 'boolean',
                                                                                                           'description': 'Include '
                                                                                                                          'created_at, '
                                                                                                                          'created_by, '
                                                                                                                          'etc. '
                                                                                                                          'columns',
                                                                                                           'default': True},
                                                                                      'flatten_arrays': {'type': 'boolean',
                                                                                                         'description': 'Flatten '
                                                                                                                        'arrays '
                                                                                                                        'into '
                                                                                                                        'multiple '
                                                                                                                        'rows '
                                                                                                                        '(cross-product)',
                                                                                                         'default': True},
                                                                                      'max_array_elements': {'type': 'integer',
                                                                                                             'maximum': 100.0,
                                                                                                             'minimum': 1.0,
                                                                                                             'description': 'Maximum '
                                                                                                                            'array '
                                                                                                                            'elements '
                                                                                                                            'to '
                                                                                                                            'include '
                                                                                                                            'when '
                                                                                                                            'flattening',
                                                                                                             'default': 10}},
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
                                                         'validate_references': {'type': 'boolean',
                                                                                 'description': 'Validate '
                                                                                                'that '
                                                                                                'terminology_ref '
                                                                                                'and '
                                                                                                'template_ref '
                                                                                                'values '
                                                                                                'exist '
                                                                                                'before '
                                                                                                'creating',
                                                                                 'default': True},
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
                                          'required': ['value', 'label'],
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
                                                   'default_value': {'description': 'Default value '
                                                                                    'if not '
                                                                                    'provided'},
                                                   'terminology_ref': {'type': 'string',
                                                                       'description': 'Canonical '
                                                                                      'terminology_id '
                                                                                      'for term '
                                                                                      'validation '
                                                                                      '(resolved '
                                                                                      'from value '
                                                                                      'at '
                                                                                      'creation)'},
                                                   'template_ref': {'type': 'string',
                                                                    'description': 'Canonical '
                                                                                   'template_id '
                                                                                   'for nested '
                                                                                   'template '
                                                                                   '(resolved from '
                                                                                   'value at '
                                                                                   'creation)'},
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
                                                                        'description': 'Canonical '
                                                                                       'template_ids '
                                                                                       'for '
                                                                                       'allowed '
                                                                                       'document '
                                                                                       'reference '
                                                                                       'targets '
                                                                                       '(resolved '
                                                                                       'from '
                                                                                       'values at '
                                                                                       'creation)'},
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
                                                                            'description': 'Canonical '
                                                                                           'terminology_ids '
                                                                                           'for '
                                                                                           'allowed '
                                                                                           'term '
                                                                                           'reference '
                                                                                           'targets '
                                                                                           '(resolved '
                                                                                           'from '
                                                                                           'values '
                                                                                           'at '
                                                                                           'creation)'},
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
                                                                                                                   "'application/pdf')",
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
                                                                             'description': 'Canonical '
                                                                                            'terminology_id '
                                                                                            'for '
                                                                                            'array '
                                                                                            'item '
                                                                                            'term '
                                                                                            'validation '
                                                                                            '(resolved '
                                                                                            'from '
                                                                                            'value '
                                                                                            'at '
                                                                                            'creation)'},
                                                   'array_template_ref': {'type': 'string',
                                                                          'description': 'Canonical '
                                                                                         'template_id '
                                                                                         'for '
                                                                                         'array '
                                                                                         'item '
                                                                                         'template '
                                                                                         '(resolved '
                                                                                         'from '
                                                                                         'value at '
                                                                                         'creation)'},
                                                   'array_file_config': {'properties': {'allowed_types': {'items': {'type': 'string'},
                                                                                                          'type': 'array',
                                                                                                          'description': 'Allowed '
                                                                                                                         'MIME '
                                                                                                                         'type '
                                                                                                                         'patterns '
                                                                                                                         '(e.g., '
                                                                                                                         "'image/*', "
                                                                                                                         "'application/pdf')",
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
                                                                                                            'fields'},
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


TOOL_DESCRIPTIONS: dict[str, str] = {
    'cancel_replay': """Cancel a replay session and delete its NATS stream.""",
    'create_document': """Create a document (an instance of a template).

Term fields: submit the human-readable value (e.g., "United Kingdom").
WIP resolves it to the term_id automatically. If resolution fails,
you'll get a clear error indicating which field/value failed.
If the template defines identity_fields and this document matches
an existing one, it creates a new version instead of a duplicate.

Fields (from OpenAPI — these are the exact field names):
template_id (string, REQUIRED): Template ID to validate against
template_version (integer): Specific template version to validate against (default: latest)
document_id (string): Pre-assigned document ID (for restore/migration — Registry uses as-is instead of generating)
version (integer): Pre-assigned version (for restore/migration — skips Registry and version computation when used with document_id)
namespace (string): Namespace for the document
data (object, REQUIRED): Document content
created_by (string): User or system creating this document
metadata (object): Custom metadata
synonyms (array of object): Optional synonym composite keys to register for this document in the Registry""",
    'create_documents_bulk': """Create multiple documents at once. Returns per-item results.

Each item follows the same schema as create_document.

Fields (from OpenAPI — these are the exact field names):
template_id (string, REQUIRED): Template ID to validate against
template_version (integer): Specific template version to validate against (default: latest)
document_id (string): Pre-assigned document ID (for restore/migration — Registry uses as-is instead of generating)
version (integer): Pre-assigned version (for restore/migration — skips Registry and version computation when used with document_id)
namespace (string): Namespace for the document
data (object, REQUIRED): Document content
created_by (string): User or system creating this document
metadata (object): Custom metadata
synonyms (array of object): Optional synonym composite keys to register for this document in the Registry""",
    'create_relationships': """Create ontology relationships between terms.

Relationship types: is_a, part_of, has_part, regulates, positively_regulates, negatively_regulates.
Use source_term_id (subject) and target_term_id (object).
Example: "Lung cancer" --is_a--> "Cancer"

Fields (from OpenAPI — these are the exact field names):
source_term_id (string, REQUIRED): The subject term ID
target_term_id (string, REQUIRED): The object term ID
relationship_type (string, REQUIRED): Relationship type value (e.g., 'is_a', 'part_of')
metadata (object): Provenance, confidence, OWL axioms
created_by (string): User or system creating this relationship""",
    'create_template': """Create a template (document schema).

IMPORTANT field naming:
  - Use "mandatory" (not "required") for required fields
  - Use "terminology_ref" (not "terminology_id") for term field references
  - Use "template_ref" (not "template_id") for reference field references
Use status: "draft" for circular dependencies, then activate_template.
Convention: use UPPER_SNAKE_CASE for the value field.

Fields (from OpenAPI — these are the exact field names):
value (string, REQUIRED): Human-readable value (e.g., 'PERSON')
label (string, REQUIRED): Display label
description (string): Detailed description
template_id (string): Pre-assigned template ID (for restore/migration — Registry uses as-is instead of generating)
version (integer): Pre-assigned version (for restore/migration — skips Registry and version computation when used with template_id)
namespace (string): Namespace for the template
extends (string): Parent template ID for inheritance
extends_version (integer): Pinned parent version (None = always use latest active parent version)
identity_fields (array of string): Fields that form the composite identity key
fields (array of object): Field definitions
  Each item:
    name (string, REQUIRED): Field name (used in data)
    label (string, REQUIRED): Human-readable label
    type (enum, REQUIRED): One of: string, number, integer, boolean, date, datetime, term, reference, file, object, array. Supported field types for template fields.
    mandatory (boolean, default: false): Whether field is required
    default_value (object): Default value if not provided
    terminology_ref (string): Canonical terminology_id for term validation (resolved from value at creation)
    template_ref (string): Canonical template_id for nested template (resolved from value at creation)
    reference_type (enum): One of: document, term, terminology, template. Type of entity being referenced (for reference type)
    target_templates (array of string): Canonical template_ids for allowed document reference targets (resolved from values at creation)
    include_subtypes (boolean): When true, target_templates also accepts documents from child templates (via inheritance)
    target_terminologies (array of string): Canonical terminology_ids for allowed term reference targets (resolved from values at creation)
    version_strategy (enum): One of: latest, pinned. How to resolve reference versions (default: latest)
    file_config (object): Configuration for file fields (allowed types, size limits)
      (nested object — see full schema)
    array_item_type (enum): One of: string, number, integer, boolean, date, datetime, term, reference, file, object, array. Type of array items (for array type)
    array_terminology_ref (string): Canonical terminology_id for array item term validation (resolved from value at creation)
    array_template_ref (string): Canonical template_id for array item template (resolved from value at creation)
    array_file_config (object): File configuration for array items if file type
      (nested object — see full schema)
    validation (object): Field-level validation rules
      (nested object — see full schema)
    semantic_type (enum): One of: email, url, latitude, longitude, percentage, duration, geo_point. Semantic type for additional validation (email, url, latitude, etc.)
    inherited (boolean): Whether this field is inherited from a parent template (set during resolution)
    inherited_from (string): Template ID of the parent template this field was inherited from
    metadata (object): Additional field metadata
rules (array of object): Cross-field validation rules
  Each item:
    type (enum, REQUIRED): One of: conditional_required, conditional_value, mutual_exclusion, dependency, pattern, range. Types of cross-field validation rules.
    description (string): Human-readable description of the rule
    conditions (array of object): Conditions that trigger the rule
      Each item:
        (nested object — see full schema)
    target_field (string): Field affected by the rule
    target_fields (array of string): Fields affected (for mutual_exclusion)
    required (boolean): For conditional_required: is field required?
    allowed_values (array of object): For conditional_value: allowed values
    pattern (string): For pattern: regex pattern
    minimum (number): For range: minimum value
    maximum (number): For range: maximum value
    error_message (string): Custom error message
metadata (object): Additional metadata
  domain (string): Business domain (e.g., 'hr', 'finance', 'healthcare')
  category (string): Template category (e.g., 'master_data', 'transaction')
  tags (array of string): Tags for categorization and search
  custom (object): Custom metadata fields
reporting (object): Configuration for PostgreSQL reporting sync
  sync_enabled (boolean, default: true): Whether to sync documents of this template to PostgreSQL
  sync_strategy (string, default: "latest_only"): Sync strategy: 'latest_only' (upsert) or 'all_versions' (insert all)
  table_name (string): Custom PostgreSQL table name (auto-generated from value if not set)
  include_metadata (boolean, default: true): Include created_at, created_by, etc. columns
  flatten_arrays (boolean, default: true): Flatten arrays into multiple rows (cross-product)
  max_array_elements (integer, default: 10): Maximum array elements to include when flattening
created_by (string): User or system creating this template
validate_references (boolean, default: true): Validate that terminology_ref and template_ref values exist before creating
status (string): Initial status: 'active' (default) or 'draft' (skips reference validation)

FieldDefinition fields:
  name (string, REQUIRED): Field name (used in data)
  label (string, REQUIRED): Human-readable label
  type (enum, REQUIRED): One of: string, number, integer, boolean, date, datetime, term, reference, file, object, array. Supported field types for template fields.
  mandatory (boolean, default: false): Whether field is required
  default_value (object): Default value if not provided
  terminology_ref (string): Canonical terminology_id for term validation (resolved from value at creation)
  template_ref (string): Canonical template_id for nested template (resolved from value at creation)
  reference_type (enum): One of: document, term, terminology, template. Type of entity being referenced (for reference type)
  target_templates (array of string): Canonical template_ids for allowed document reference targets (resolved from values at creation)
  include_subtypes (boolean): When true, target_templates also accepts documents from child templates (via inheritance)
  target_terminologies (array of string): Canonical terminology_ids for allowed term reference targets (resolved from values at creation)
  version_strategy (enum): One of: latest, pinned. How to resolve reference versions (default: latest)
  file_config (object): Configuration for file fields (allowed types, size limits)
    (nested object — see full schema)
  array_item_type (enum): One of: string, number, integer, boolean, date, datetime, term, reference, file, object, array. Type of array items (for array type)
  array_terminology_ref (string): Canonical terminology_id for array item term validation (resolved from value at creation)
  array_template_ref (string): Canonical template_id for array item template (resolved from value at creation)
  array_file_config (object): File configuration for array items if file type
    (nested object — see full schema)
  validation (object): Field-level validation rules
    (nested object — see full schema)
  semantic_type (enum): One of: email, url, latitude, longitude, percentage, duration, geo_point. Semantic type for additional validation (email, url, latitude, etc.)
  inherited (boolean): Whether this field is inherited from a parent template (set during resolution)
  inherited_from (string): Template ID of the parent template this field was inherited from
  metadata (object): Additional field metadata""",
    'create_templates_bulk': """Create multiple templates. Use status: 'draft' for circular dependencies, then activate.

Each item follows the same schema as create_template.
IMPORTANT: Use "mandatory" (not "required"), "terminology_ref" (not "terminology_id").

Fields (from OpenAPI — these are the exact field names):
value (string, REQUIRED): Human-readable value (e.g., 'PERSON')
label (string, REQUIRED): Display label
description (string): Detailed description
template_id (string): Pre-assigned template ID (for restore/migration — Registry uses as-is instead of generating)
version (integer): Pre-assigned version (for restore/migration — skips Registry and version computation when used with template_id)
namespace (string): Namespace for the template
extends (string): Parent template ID for inheritance
extends_version (integer): Pinned parent version (None = always use latest active parent version)
identity_fields (array of string): Fields that form the composite identity key
fields (array of object): Field definitions
  Each item:
    name (string, REQUIRED): Field name (used in data)
    label (string, REQUIRED): Human-readable label
    type (enum, REQUIRED): One of: string, number, integer, boolean, date, datetime, term, reference, file, object, array. Supported field types for template fields.
    mandatory (boolean, default: false): Whether field is required
    default_value (object): Default value if not provided
    terminology_ref (string): Canonical terminology_id for term validation (resolved from value at creation)
    template_ref (string): Canonical template_id for nested template (resolved from value at creation)
    reference_type (enum): One of: document, term, terminology, template. Type of entity being referenced (for reference type)
    target_templates (array of string): Canonical template_ids for allowed document reference targets (resolved from values at creation)
    include_subtypes (boolean): When true, target_templates also accepts documents from child templates (via inheritance)
    target_terminologies (array of string): Canonical terminology_ids for allowed term reference targets (resolved from values at creation)
    version_strategy (enum): One of: latest, pinned. How to resolve reference versions (default: latest)
    file_config (object): Configuration for file fields (allowed types, size limits)
      (nested object — see full schema)
    array_item_type (enum): One of: string, number, integer, boolean, date, datetime, term, reference, file, object, array. Type of array items (for array type)
    array_terminology_ref (string): Canonical terminology_id for array item term validation (resolved from value at creation)
    array_template_ref (string): Canonical template_id for array item template (resolved from value at creation)
    array_file_config (object): File configuration for array items if file type
      (nested object — see full schema)
    validation (object): Field-level validation rules
      (nested object — see full schema)
    semantic_type (enum): One of: email, url, latitude, longitude, percentage, duration, geo_point. Semantic type for additional validation (email, url, latitude, etc.)
    inherited (boolean): Whether this field is inherited from a parent template (set during resolution)
    inherited_from (string): Template ID of the parent template this field was inherited from
    metadata (object): Additional field metadata
rules (array of object): Cross-field validation rules
  Each item:
    type (enum, REQUIRED): One of: conditional_required, conditional_value, mutual_exclusion, dependency, pattern, range. Types of cross-field validation rules.
    description (string): Human-readable description of the rule
    conditions (array of object): Conditions that trigger the rule
      Each item:
        (nested object — see full schema)
    target_field (string): Field affected by the rule
    target_fields (array of string): Fields affected (for mutual_exclusion)
    required (boolean): For conditional_required: is field required?
    allowed_values (array of object): For conditional_value: allowed values
    pattern (string): For pattern: regex pattern
    minimum (number): For range: minimum value
    maximum (number): For range: maximum value
    error_message (string): Custom error message
metadata (object): Additional metadata
  domain (string): Business domain (e.g., 'hr', 'finance', 'healthcare')
  category (string): Template category (e.g., 'master_data', 'transaction')
  tags (array of string): Tags for categorization and search
  custom (object): Custom metadata fields
reporting (object): Configuration for PostgreSQL reporting sync
  sync_enabled (boolean, default: true): Whether to sync documents of this template to PostgreSQL
  sync_strategy (string, default: "latest_only"): Sync strategy: 'latest_only' (upsert) or 'all_versions' (insert all)
  table_name (string): Custom PostgreSQL table name (auto-generated from value if not set)
  include_metadata (boolean, default: true): Include created_at, created_by, etc. columns
  flatten_arrays (boolean, default: true): Flatten arrays into multiple rows (cross-product)
  max_array_elements (integer, default: 10): Maximum array elements to include when flattening
created_by (string): User or system creating this template
validate_references (boolean, default: true): Validate that terminology_ref and template_ref values exist before creating
status (string): Initial status: 'active' (default) or 'draft' (skips reference validation)

FieldDefinition fields:
  name (string, REQUIRED): Field name (used in data)
  label (string, REQUIRED): Human-readable label
  type (enum, REQUIRED): One of: string, number, integer, boolean, date, datetime, term, reference, file, object, array. Supported field types for template fields.
  mandatory (boolean, default: false): Whether field is required
  default_value (object): Default value if not provided
  terminology_ref (string): Canonical terminology_id for term validation (resolved from value at creation)
  template_ref (string): Canonical template_id for nested template (resolved from value at creation)
  reference_type (enum): One of: document, term, terminology, template. Type of entity being referenced (for reference type)
  target_templates (array of string): Canonical template_ids for allowed document reference targets (resolved from values at creation)
  include_subtypes (boolean): When true, target_templates also accepts documents from child templates (via inheritance)
  target_terminologies (array of string): Canonical terminology_ids for allowed term reference targets (resolved from values at creation)
  version_strategy (enum): One of: latest, pinned. How to resolve reference versions (default: latest)
  file_config (object): Configuration for file fields (allowed types, size limits)
    (nested object — see full schema)
  array_item_type (enum): One of: string, number, integer, boolean, date, datetime, term, reference, file, object, array. Type of array items (for array type)
  array_terminology_ref (string): Canonical terminology_id for array item term validation (resolved from value at creation)
  array_template_ref (string): Canonical template_id for array item template (resolved from value at creation)
  array_file_config (object): File configuration for array items if file type
    (nested object — see full schema)
  validation (object): Field-level validation rules
    (nested object — see full schema)
  semantic_type (enum): One of: email, url, latitude, longitude, percentage, duration, geo_point. Semantic type for additional validation (email, url, latitude, etc.)
  inherited (boolean): Whether this field is inherited from a parent template (set during resolution)
  inherited_from (string): Template ID of the parent template this field was inherited from
  metadata (object): Additional field metadata""",
    'create_terminologies_bulk': """Create multiple terminologies at once.

Each item follows the same schema as create_terminology.

Fields (from OpenAPI — these are the exact field names):
value (string, REQUIRED): Human-readable value (e.g., 'DOC_STATUS')
label (string, REQUIRED): Display label
description (string): Detailed description
namespace (string): Namespace for the terminology
case_sensitive (boolean, default: false): Whether term values are case-sensitive
allow_multiple (boolean, default: false): Whether multiple terms can be selected
extensible (boolean, default: false): Whether users can add new terms at runtime
metadata (object): Additional metadata
  source (string): Source of the terminology (e.g., 'ISO 3166', 'internal')
  source_url (string): URL to the source specification
  version (string): Version of the terminology (e.g., '2024.1')
  language (string, default: "en"): Primary language code (ISO 639-1)
  custom (object): Custom metadata fields
created_by (string): User or system creating this terminology""",
    'create_terminology': """Create a terminology (controlled vocabulary).

Convention: use UPPER_SNAKE_CASE for the value field.
A terminology is a namespace for terms — e.g., COUNTRY, GENDER, DIAGNOSIS_CODE.

Fields (from OpenAPI — these are the exact field names):
value (string, REQUIRED): Human-readable value (e.g., 'DOC_STATUS')
label (string, REQUIRED): Display label
description (string): Detailed description
namespace (string): Namespace for the terminology
case_sensitive (boolean, default: false): Whether term values are case-sensitive
allow_multiple (boolean, default: false): Whether multiple terms can be selected
extensible (boolean, default: false): Whether users can add new terms at runtime
metadata (object): Additional metadata
  source (string): Source of the terminology (e.g., 'ISO 3166', 'internal')
  source_url (string): URL to the source specification
  version (string): Version of the terminology (e.g., '2024.1')
  language (string, default: "en"): Primary language code (ISO 639-1)
  custom (object): Custom metadata fields
created_by (string): User or system creating this terminology""",
    'create_terms': """Create terms in a terminology.

Terms are entries in a terminology (e.g., "GB" in COUNTRY, "Male" in GENDER).
Each term must have a 'value' field (unique within the terminology).
Optional: aliases (list of strings), label, description, sort_order.

Fields (from OpenAPI — these are the exact field names):
value (string, REQUIRED): The value stored in documents (unique within terminology)
aliases (array of string): Alternative values that resolve to this term (e.g., ['MR.', 'mr'])
label (string): Display label for UI. Defaults to value if not provided.
description (string): Detailed description
sort_order (integer, default: 0): Sort order within terminology
parent_term_id (string): Parent term ID for hierarchical terms
translations (array of object): Translations
  Each item:
    language (string, REQUIRED): Language code (ISO 639-1)
    label (string, REQUIRED): Translated display label
    description (string): Translated description
metadata (object): Custom metadata
created_by (string): User or system creating this term""",
    'get_replay_status': """Get the status of a replay session.""",
    'get_template_fields': """Get a clean summary of a template's fields for querying or document creation.

Returns template_id, field names, types, mandatory flags, and references.
Use this to understand what data a template holds before querying.""",
    'import_documents_csv': """Import documents from a CSV/XLSX file into a template.

Reads a local CSV/XLSX file, maps columns to template fields,
and creates documents in bulk. Auto-maps columns if mapping is omitted.
Term fields: use human-readable values in the CSV.""",
    'import_terminology': """Import a terminology with terms from JSON data.

The import format matches the export format from export_terminology.""",
    'list_report_tables': """List available PostgreSQL reporting tables and their schemas.

Returns doc_* tables (one per template) plus terminologies, terms, term_relationships.
Use this before run_report_query to understand available tables and columns.""",
    'query_by_template': """Query documents by template value with easy field filtering.

Resolves template_value → template_id automatically.
Field names auto-prefixed with "data." — write "country" not "data.country".
field_filters: [{"field": "country", "operator": "eq", "value": "CH"}]
Operators: eq, ne, gt, gte, lt, lte, in, nin, exists, regex.""",
    'query_documents': """Query documents with complex filters (low-level). Prefer query_by_template.

filters.filters: [{field: "data.country", operator: "eq", value: "CH"}]
Operators: eq, ne, gt, gte, lt, lte, in, nin, exists, regex.
Field names MUST include "data." prefix for document data fields.
Use query_by_template for easier querying (auto-prefixes fields).""",
    'run_report_query': """Execute a read-only SQL query against the PostgreSQL reporting database.

For cross-template JOINs, aggregations, and analytics.
Tables: doc_{template_value} (e.g., doc_patient).
Term fields: {field} (value) + {field}_term_id columns.
Use $1, $2 for parameter binding. Read-only: no INSERT/UPDATE/DELETE.
Timeout: 30s default. Max rows: 1000 default.""",
    'start_replay': """Start replaying stored documents as NATS events.

For onboarding new consumers or backfilling data.
Events go to a separate NATS stream with metadata.replay=true.
Use get_replay_status to track progress, cancel_replay to stop.""",
    'upload_file': """Upload a file to WIP from a local path.

Reads a file from disk and uploads it to WIP's MinIO-backed file storage.
Supports any file type. Returns file_id for use in document file fields.
Tags are comma-separated (e.g., "receipt,2024,tax").""",
}
