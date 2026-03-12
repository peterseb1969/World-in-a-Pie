import type { Template, FieldDefinition, FieldType } from '../types/template.js'

export type FormInputType =
  | 'text'
  | 'number'
  | 'integer'
  | 'checkbox'
  | 'date'
  | 'datetime'
  | 'select'
  | 'search'
  | 'file'
  | 'group'
  | 'list'

export interface FormField {
  name: string
  label: string
  inputType: FormInputType
  required: boolean
  defaultValue?: unknown
  isIdentity: boolean
  /** For term/select fields */
  terminologyCode?: string
  /** For reference/search fields */
  referenceType?: string
  targetTemplates?: string[]
  targetTerminologies?: string[]
  /** For file fields */
  fileConfig?: {
    allowedTypes: string[]
    maxSizeMb: number
    multiple: boolean
    maxFiles?: number
  }
  /** For array fields */
  arrayItemType?: FormInputType
  arrayTerminologyCode?: string
  /** For object/group fields */
  children?: FormField[]
  /** Validation */
  validation?: {
    pattern?: string
    minLength?: number
    maxLength?: number
    minimum?: number
    maximum?: number
    enum?: unknown[]
  }
  semanticType?: string
}

const FIELD_TYPE_TO_INPUT: Record<FieldType, FormInputType> = {
  string: 'text',
  number: 'number',
  integer: 'integer',
  boolean: 'checkbox',
  date: 'date',
  datetime: 'datetime',
  term: 'select',
  reference: 'search',
  file: 'file',
  object: 'group',
  array: 'list',
}

function fieldToFormField(field: FieldDefinition, identityFields: string[]): FormField {
  const inputType = FIELD_TYPE_TO_INPUT[field.type] ?? 'text'

  const formField: FormField = {
    name: field.name,
    label: field.label,
    inputType,
    required: field.mandatory,
    isIdentity: identityFields.includes(field.name),
  }

  if (field.default_value !== undefined) formField.defaultValue = field.default_value
  if (field.terminology_ref) formField.terminologyCode = field.terminology_ref
  if (field.reference_type) formField.referenceType = field.reference_type
  if (field.target_templates?.length) formField.targetTemplates = field.target_templates
  if (field.target_terminologies?.length) formField.targetTerminologies = field.target_terminologies
  if (field.semantic_type) formField.semanticType = field.semantic_type

  if (field.file_config) {
    formField.fileConfig = {
      allowedTypes: field.file_config.allowed_types,
      maxSizeMb: field.file_config.max_size_mb,
      multiple: field.file_config.multiple,
      maxFiles: field.file_config.max_files,
    }
  }

  if (field.array_item_type) {
    formField.arrayItemType = FIELD_TYPE_TO_INPUT[field.array_item_type] ?? 'text'
  }
  if (field.array_terminology_ref) formField.arrayTerminologyCode = field.array_terminology_ref

  if (field.validation) {
    formField.validation = {
      pattern: field.validation.pattern,
      minLength: field.validation.min_length,
      maxLength: field.validation.max_length,
      minimum: field.validation.minimum,
      maximum: field.validation.maximum,
      enum: field.validation.enum,
    }
  }

  return formField
}

/** Convert a WIP Template into a framework-agnostic form field descriptor array. */
export function templateToFormSchema(template: Template): FormField[] {
  return template.fields.map((f) => fieldToFormField(f, template.identity_fields))
}
