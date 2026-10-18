import { Skeleton } from './ui/Skeleton'

export function PageLoader({ blocks = 3 }: { blocks?: number }) {
  return (
    <div className="mx-auto w-full max-w-[1500px] px-4 py-6 sm:px-6 lg:px-8" role="status" aria-label="Loading view">
      <Skeleton className="h-7 w-64" />
      <Skeleton className="mt-3 h-4 w-96 max-w-full" />
      <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: blocks * 2 }).map((_, index) => (
          <Skeleton key={index} className="h-24" />
        ))}
      </div>
      <Skeleton className="mt-4 h-72" />
    </div>
  )
}
