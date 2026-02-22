"""
GitHub Tools for Microsoft Agent Framework
Provides typed tool functions to interact with GitHub repositories
"""

from typing import Annotated
from operations.trading_platform_operations import GitHubOperations

# Initialize GitHub operations
github_operations = GitHubOperations()


def get_repos_by_user(
    user: Annotated[str, "The GitHub username to get repositories for"]
) -> str:
    """
    Get a list of repositories from a GitHub user account.
    Use this when you need to see what repositories a user has.
    
    Args:
        user: The GitHub username to get repositories for
        
    Returns:
        A formatted string with the list of repositories
    """
    try:
        repos = github_operations.get_repo_list_by_username(user)
        if repos:
            return f"Found {len(repos)} repositories for user '{user}':\n" + "\n".join(f"- {repo}" for repo in repos)
        else:
            return f"No repositories found for user '{user}'"
    except Exception as e:
        return f"Error getting repositories for user '{user}': {str(e)}"


def get_files_by_repo(
    repo: Annotated[str, "The repository in format 'username/repo_name'"]
) -> str:
    """
    Get a list of files in a GitHub repository.
    The repository should be in the format 'username/repo_name'.
    Use this before getting file content to see what files are available.
    
    Args:
        repo: The repository in format 'username/repo_name'
        
    Returns:
        A formatted string with the list of files
    """
    try:
        files = github_operations.get_file_list_by_repo(repo)
        if files:
            return f"Found {len(files)} files in repository '{repo}':\n" + "\n".join(f"- {file}" for file in files)
        else:
            return f"No files found in repository '{repo}'"
    except Exception as e:
        return f"Error getting files for repository '{repo}': {str(e)}"


def get_file_content(
    repo: Annotated[str, "The repository in format 'username/repo_name'"],
    path: Annotated[str, "The file path within the repository"]
) -> str:
    """
    Get the content of a specific file from a GitHub repository.
    The repository should be in the format 'username/repo_name' and 
    path should be the file path within the repository.
    
    Args:
        repo: The repository in format 'username/repo_name'
        path: The file path within the repository
        
    Returns:
        The file content as a string
    """
    try:
        content = github_operations.get_file_content_by_repo_and_path(repo, path)
        if content:
            return f"Content of file '{path}' from repository '{repo}':\n\n{content}"
        else:
            return f"File '{path}' not found in repository '{repo}'"
    except Exception as e:
        return f"Error getting file content for '{path}' in repository '{repo}': {str(e)}"


def create_github_issue(
    repo: Annotated[str, "The repository in format 'username/repo_name'"],
    title: Annotated[str, "The issue title"],
    body: Annotated[str, "The issue description/body"]
) -> str:
    """
    Create a new issue in a GitHub repository.
    The repository should be in the format 'username/repo_name'.
    Use this to report bugs or request features.
    
    Args:
        repo: The repository in format 'username/repo_name'
        title: The issue title
        body: The issue description/body
        
    Returns:
        Confirmation message with issue URL
    """
    try:
        result = github_operations.create_issue(repo, title, body)
        return result
    except Exception as e:
        return f"Error creating issue in repository '{repo}': {str(e)}"

