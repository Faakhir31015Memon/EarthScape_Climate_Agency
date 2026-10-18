import { clsx, type ClassValue } from 'clsx'

export function cn(...inputs: ClassValue[]): string {
  return clsx(inputs)
}

export function cx(...inputs: ClassValue[]): string {
  return clsx(inputs)
}
