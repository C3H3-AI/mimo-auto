import { Skeleton, Box } from "@mui/material";

interface LoadingSkeletonProps {
  lines?: number;
  variant?: "text" | "chat" | "list";
}

export function LoadingSkeleton({
  lines = 3,
  variant = "text",
}: LoadingSkeletonProps) {
  if (variant === "chat") {
    return (
      <Box className="flex flex-col gap-4 p-4">
        {/* User message skeleton */}
        <Box className="flex justify-end">
          <Box className="max-w-[70%]">
            <Skeleton
              variant="rounded"
              width={200}
              height={40}
              animation="wave"
            />
          </Box>
        </Box>
        {/* Assistant message skeleton */}
        <Box className="flex justify-start">
          <Box className="max-w-[70%] space-y-2">
            <Skeleton
              variant="rounded"
              width={120}
              height={20}
              animation="wave"
            />
            <Skeleton
              variant="rounded"
              width={300}
              height={16}
              animation="wave"
            />
            <Skeleton
              variant="rounded"
              width={250}
              height={16}
              animation="wave"
            />
            <Skeleton
              variant="rounded"
              width={180}
              height={16}
              animation="wave"
            />
          </Box>
        </Box>
      </Box>
    );
  }

  if (variant === "list") {
    return (
      <Box className="flex flex-col gap-2 p-2">
        {Array.from({ length: lines }).map((_, i) => (
          <Skeleton
            key={i}
            variant="rounded"
            height={48}
            animation="wave"
            sx={{ width: "100%" }}
          />
        ))}
      </Box>
    );
  }

  return (
    <Box className="flex flex-col gap-2 p-2">
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton
          key={i}
          variant="text"
          height={20}
          animation="wave"
          sx={{ width: `${60 + (i * 13) % 40}%` }}
        />
      ))}
    </Box>
  );
}
