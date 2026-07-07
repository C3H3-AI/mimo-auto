import { useState, useEffect, useCallback } from "react";
import {
  Box,
  Typography,
  IconButton,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  CircularProgress,
  Tooltip,
  Breadcrumbs,
  Link,
} from "@mui/material";
import FolderIcon from "@mui/icons-material/Folder";
import InsertDriveFileIcon from "@mui/icons-material/InsertDriveFile";
import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import RefreshIcon from "@mui/icons-material/Refresh";
import CodeIcon from "@mui/icons-material/Code";
import TextSnippetIcon from "@mui/icons-material/TextSnippet";
import ImageIcon from "@mui/icons-material/Image";
import { MimoClient } from "../api/mimoClient";
import type { FileEntry } from "../types";

function formatSize(bytes?: number): string {
  if (bytes === undefined || bytes === null) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function getFileIcon(entry: FileEntry) {
  if (entry.type === "directory") return <FolderIcon fontSize="small" sx={{ color: "#fbbc04" }} />;
  const ext = entry.name.split(".").pop()?.toLowerCase() || "";
  if (["ts", "tsx", "js", "jsx", "py", "rs", "go", "java", "c", "cpp", "h"].includes(ext))
    return <CodeIcon fontSize="small" sx={{ color: "#4285f4" }} />;
  if (["json", "yaml", "yml", "toml", "xml", "csv"].includes(ext))
    return <TextSnippetIcon fontSize="small" sx={{ color: "#34a853" }} />;
  if (["png", "jpg", "jpeg", "gif", "svg", "webp"].includes(ext))
    return <ImageIcon fontSize="small" sx={{ color: "#ea4335" }} />;
  return <InsertDriveFileIcon fontSize="small" sx={{ color: "#9aa0a6" }} />;
}

interface FileViewerProps {
  filePath: string;
  onClose: () => void;
}

function FileViewer({ filePath, onClose }: FileViewerProps) {
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    MimoClient.readFile(filePath)
      .then((data) => setContent(typeof data === "string" ? data : JSON.stringify(data, null, 2)))
      .catch(() => setContent("Failed to load file"))
      .finally(() => setLoading(false));
  }, [filePath]);

  return (
    <Box sx={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <Box sx={{ p: 1, borderBottom: "1px solid", borderColor: "divider", display: "flex", alignItems: "center", gap: 1 }}>
        <IconButton size="small" onClick={onClose}><ArrowBackIcon fontSize="small" /></IconButton>
        <Typography variant="body2" sx={{ fontFamily: "monospace", overflow: "hidden", textOverflow: "ellipsis" }}>
          {filePath}
        </Typography>
      </Box>
      <Box sx={{ flex: 1, overflow: "auto", p: 2, bgcolor: "action.hover" }}>
        {loading ? (
          <Box sx={{ display: "flex", justifyContent: "center", p: 4 }}><CircularProgress size={24} /></Box>
        ) : (
          <pre style={{ margin: 0, fontSize: "0.8rem", fontFamily: "monospace", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
            {content}
          </pre>
        )}
      </Box>
    </Box>
  );
}

interface FileExplorerProps {
  compact?: boolean;
}

export function FileExplorer({ compact = false }: FileExplorerProps) {
  const [entries, setEntries] = useState<FileEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [currentPath, setCurrentPath] = useState("");
  const [viewingFile, setViewingFile] = useState<string | null>(null);

  const loadDirectory = useCallback(async (path: string) => {
    setLoading(true);
    try {
      const data = await MimoClient.listFiles(path);
      // mimo serve returns array of {name, path, type, ...}
      const items = Array.isArray(data) ? data : [];
      setEntries(items.map((item: any) => ({
        name: item.name || item.path,
        path: item.path || item.name,
        type: item.type || (item.ignored === false ? "directory" : "file"),
        size: item.size,
        modified: item.modified,
      })));
      setCurrentPath(path);
    } catch {
      setEntries([]);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    loadDirectory("");
  }, [loadDirectory]);

  const handleClick = (entry: FileEntry) => {
    if (entry.type === "directory") {
      loadDirectory(entry.path);
    } else {
      setViewingFile(entry.path);
    }
  };

  const handleBack = () => {
    const parts = currentPath.split("/").filter(Boolean);
    parts.pop();
    loadDirectory(parts.join("/"));
  };

  // If viewing a file, show the viewer
  if (viewingFile) {
    return <FileViewer filePath={viewingFile} onClose={() => setViewingFile(null)} />;
  }

  const pathParts = currentPath.split("/").filter(Boolean);

  return (
    <Box sx={{ height: "100%", display: "flex", flexDirection: "column" }}>
      {/* Header */}
      <Box sx={{ p: 1, borderBottom: "1px solid", borderColor: "divider", display: "flex", alignItems: "center", gap: 1 }}>
        {currentPath && (
          <IconButton size="small" onClick={handleBack} title="Go back">
            <ArrowBackIcon fontSize="small" />
          </IconButton>
        )}
        <Typography variant="subtitle2" sx={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {currentPath || "Project Files"}
        </Typography>
        <IconButton size="small" onClick={() => loadDirectory(currentPath)} title="Refresh">
          <RefreshIcon fontSize="small" />
        </IconButton>
      </Box>

      {/* Breadcrumbs */}
      {pathParts.length > 0 && !compact && (
        <Box sx={{ px: 1, py: 0.5, borderBottom: "1px solid", borderColor: "divider" }}>
          <Breadcrumbs sx={{ fontSize: "0.75rem" }}>
            <Link
              component="button"
              variant="body2"
              onClick={() => loadDirectory("")}
              sx={{ fontSize: "0.75rem", cursor: "pointer" }}
            >
              root
            </Link>
            {pathParts.map((part, i) => (
              <Link
                key={i}
                component="button"
                variant="body2"
                onClick={() => loadDirectory(pathParts.slice(0, i + 1).join("/"))}
                sx={{ fontSize: "0.75rem", cursor: "pointer" }}
              >
                {part}
              </Link>
            ))}
          </Breadcrumbs>
        </Box>
      )}

      {/* File List */}
      <Box sx={{ flex: 1, overflow: "auto" }}>
        {loading ? (
          <Box sx={{ display: "flex", justifyContent: "center", p: 4 }}><CircularProgress size={24} /></Box>
        ) : entries.length === 0 ? (
          <Box sx={{ p: 3, textAlign: "center" }}>
            <Typography variant="body2" color="text.secondary">No files</Typography>
          </Box>
        ) : (
          <List dense disablePadding>
            {entries.map((entry) => (
              <ListItemButton
                key={entry.path}
                onClick={() => handleClick(entry)}
                sx={{ py: 0.25, px: 1 }}
              >
                <ListItemIcon sx={{ minWidth: 28 }}>{getFileIcon(entry)}</ListItemIcon>
                <ListItemText
                  primary={entry.name}
                  secondary={entry.type === "file" ? formatSize(entry.size) : undefined}
                  primaryTypographyProps={{ variant: "body2", noWrap: true }}
                  secondaryTypographyProps={{ variant: "caption" }}
                />
              </ListItemButton>
            ))}
          </List>
        )}
      </Box>
    </Box>
  );
}
