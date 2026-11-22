/*
 * PROJECT:     ReactOS api tests
 * LICENSE:     GPL-2.0-or-later (https://spdx.org/licenses/GPL-2.0-or-later)
 * PURPOSE:     Tests for RegCreateKeyExW.
 * COPYRIGHT:   Copyright 2023 Doug Lyons <douglyons@douglyons.com>
 */

/*
 * Idea based loosely on code from the following:
 * https://learn.microsoft.com/en-us/windows/win32/secauthz/creating-a-security-descriptor-for-a-new-object-in-c--
 */

#include <apitest.h>
#include <stdio.h>

#define WIN32_NO_STATUS
#define _INC_WINDOWS
#define COM_NO_WINDOWS_H
#include <windef.h>
#include <aclapi.h>

static void TestRestoreCreate(void)
{
    static const WCHAR Path[] = L"Software\\LiberNT_RegCreateRestore";
    HANDLE Token = NULL, Duplicate = NULL, Existing;
    HKEY Parent = NULL, Child = NULL, Query;
    SECURITY_DESCRIPTOR Denied, Allowed;
    SECURITY_ATTRIBUTES Attributes;
    ACL EmptyAcl;
    TOKEN_PRIVILEGES Privilege;
    DWORD Disposition, Value = 0x12345678, ReadValue, Size, Type;
    LONG Error;
    BOOL Impersonating = FALSE;

    if (OpenThreadToken(GetCurrentThread(), TOKEN_QUERY, TRUE, &Existing))
    {
        CloseHandle(Existing);
        skip("Restore-create test requires a non-impersonating caller\n");
        return;
    }
    if (GetLastError() != ERROR_NO_TOKEN ||
        !OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY | TOKEN_DUPLICATE, &Token))
    {
        skip("Cannot obtain the process token: %lu\n", GetLastError());
        return;
    }
    if (!DuplicateTokenEx(Token, TOKEN_QUERY | TOKEN_IMPERSONATE | TOKEN_ADJUST_PRIVILEGES,
                          NULL, SecurityImpersonation, TokenImpersonation, &Duplicate))
        goto Cleanup;

    Privilege.PrivilegeCount = 1;
    if (!LookupPrivilegeValue(NULL, SE_RESTORE_NAME, &Privilege.Privileges[0].Luid))
        goto Cleanup;
    Privilege.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED;
    SetLastError(ERROR_SUCCESS);
    if (!AdjustTokenPrivileges(Duplicate, FALSE, &Privilege, 0, NULL, NULL) ||
        GetLastError() != ERROR_SUCCESS)
    {
        skip("SeRestorePrivilege is unavailable; run elevated\n");
        goto Cleanup;
    }

    Error = RegCreateKeyExW(HKEY_CURRENT_USER, Path, 0, NULL, 0, KEY_ALL_ACCESS,
                            NULL, &Parent, &Disposition);
    ok_dec(Error, ERROR_SUCCESS);
    if (Error != ERROR_SUCCESS)
        goto Cleanup;
    if (Disposition != REG_CREATED_NEW_KEY)
    {
        skip("Restore-create test key already exists\n");
        RegCloseKey(Parent);
        Parent = NULL;
        goto Cleanup;
    }
    if (!InitializeAcl(&EmptyAcl, sizeof(EmptyAcl), ACL_REVISION) ||
        !InitializeSecurityDescriptor(&Denied, SECURITY_DESCRIPTOR_REVISION) ||
        !SetSecurityDescriptorDacl(&Denied, TRUE, &EmptyAcl, FALSE) ||
        !InitializeSecurityDescriptor(&Allowed, SECURITY_DESCRIPTOR_REVISION) ||
        !SetSecurityDescriptorDacl(&Allowed, TRUE, NULL, FALSE))
        goto Cleanup;

    Error = RegSetKeySecurity(Parent, DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION, &Denied);
    ok_dec(Error, ERROR_SUCCESS);
    if (Error != ERROR_SUCCESS)
        goto Cleanup;
    Attributes.nLength = sizeof(Attributes);
    Attributes.lpSecurityDescriptor = &Allowed;
    Attributes.bInheritHandle = FALSE;
    if (!SetThreadToken(NULL, Duplicate))
        goto Cleanup;
    Impersonating = TRUE;

    Error = RegCreateKeyExW(Parent, L"Child", 0, NULL, 0, KEY_ALL_ACCESS,
                            &Attributes, &Child, NULL);
    ok_dec(Error, ERROR_ACCESS_DENIED);
    if (Error == ERROR_SUCCESS)
    {
        RegCloseKey(Child);
        Child = NULL;
        RegDeleteKeyW(Parent, L"Child");
    }
    Error = RegCreateKeyExW(Parent, L"Child", 0, NULL, REG_OPTION_BACKUP_RESTORE,
                            KEY_ALL_ACCESS, &Attributes, &Child, &Disposition);
    ok_dec(Error, ERROR_SUCCESS);
    if (Error == ERROR_SUCCESS)
    {
        ok_dec(Disposition, REG_CREATED_NEW_KEY);
        Error = RegSetValueExW(Child, L"Value", 0, REG_DWORD, (BYTE *)&Value, sizeof(Value));
        ok_dec(Error, ERROR_SUCCESS);
        Error = RegOpenKeyExW(Child, L"", 0, KEY_QUERY_VALUE, &Query);
        ok_dec(Error, ERROR_SUCCESS);
        if (Error == ERROR_SUCCESS)
        {
            Size = sizeof(ReadValue);
            Error = RegQueryValueExW(Query, L"Value", NULL, &Type, (BYTE *)&ReadValue, &Size);
            ok_dec(Error, ERROR_SUCCESS);
            if (Error == ERROR_SUCCESS)
            {
                ok_dec(Type, REG_DWORD);
                ok_dec(Size, sizeof(Value));
                ok_hex(ReadValue, Value);
            }
            RegCloseKey(Query);
        }
    }

Cleanup:
    if (Impersonating)
        ok(RevertToSelf(), "Cannot restore the thread token: %lu\n", GetLastError());
    if (Child)
        RegCloseKey(Child);
    if (Parent)
    {
        if (InitializeSecurityDescriptor(&Allowed, SECURITY_DESCRIPTOR_REVISION) &&
            SetSecurityDescriptorDacl(&Allowed, TRUE, NULL, FALSE))
            ok_dec(RegSetKeySecurity(Parent, DACL_SECURITY_INFORMATION, &Allowed), ERROR_SUCCESS);
        RegDeleteKeyW(Parent, L"Child");
        RegCloseKey(Parent);
        ok_dec(RegDeleteKeyW(HKEY_CURRENT_USER, Path), ERROR_SUCCESS);
    }
    if (Duplicate)
        CloseHandle(Duplicate);
    if (Token)
        CloseHandle(Token);
}

START_TEST(RegCreateKeyEx)
{
    HKEY hkey_main;
    DWORD dwRes, dwDisposition;
    PACL pACL = NULL;
    PSECURITY_DESCRIPTOR pSD = NULL;
    PSID pEveryoneSID = NULL, pAdminSID = NULL;
    SID_IDENTIFIER_AUTHORITY SIDAuthWorld = {SECURITY_WORLD_SID_AUTHORITY};
    SID_IDENTIFIER_AUTHORITY SIDAuthNT = {SECURITY_NT_AUTHORITY};
    EXPLICIT_ACCESSW ea[2];
    SECURITY_ATTRIBUTES sa = { 0 };
    LONG lRes;
    BOOL bRes;
    LONG ErrorCode = 0;
    HKEY hkSub = NULL;

    TestRestoreCreate();

    // If any of the test keys already exist, delete them to ensure proper testing
    if (RegOpenKeyExW(HKEY_CURRENT_USER, L"mykey", 0, KEY_ALL_ACCESS, &hkey_main) == ERROR_SUCCESS)
    {
        RegCloseKey(hkey_main);
        ErrorCode = RegDeleteKeyW(HKEY_CURRENT_USER, L"mykey");
        ok_dec(ErrorCode, ERROR_SUCCESS);
        if (ErrorCode != ERROR_SUCCESS)
        {
            skip("'HKCU\\mykey' cannot be deleted. Terminating test\n");
            goto Cleanup;
        }
    }

    if (RegOpenKeyExW(HKEY_CURRENT_USER, L"mykey1", 0, KEY_ALL_ACCESS, &hkey_main) == ERROR_SUCCESS)
    {
        RegCloseKey(hkey_main);
        ErrorCode = RegDeleteKeyW(HKEY_CURRENT_USER, L"mykey1");
        ok_dec(ErrorCode, ERROR_SUCCESS);
        if (ErrorCode != ERROR_SUCCESS)
        {
            skip("'HKCU\\mykey1' cannot be deleted. Terminating test\n");
            goto Cleanup;
        }
    }

    if (RegOpenKeyExW(HKEY_CURRENT_USER, L"mykey2", 0, KEY_ALL_ACCESS, &hkey_main) == ERROR_SUCCESS)
    {
        RegCloseKey(hkey_main);
        ErrorCode = RegDeleteKeyW(HKEY_CURRENT_USER, L"mykey2");
        ok_dec(ErrorCode, ERROR_SUCCESS);
        if (ErrorCode != ERROR_SUCCESS)
        {
            skip("'HKCU\\mykey2' cannot be deleted. Terminating test\n");
            goto Cleanup;
        }
    }

    // Setup GetLastError to known value for tests
    SetLastError(0xdeadbeef);

    // Create a well-known SID for the Everyone group.
    bRes = AllocateAndInitializeSid(&SIDAuthWorld, 1,
           SECURITY_WORLD_RID,
           0, 0, 0, 0, 0, 0, 0,
           &pEveryoneSID);
    ok(bRes, "AllocateAndInitializeSid Error %ld\n", GetLastError());
    if (!bRes)
    {
        skip("EveryoneSID not initialized. Terminating test\n");
        goto Cleanup;
    }

    // Initialize an EXPLICIT_ACCESS structure for an ACE.
    // The ACE will allow Everyone read access to the key.
    ZeroMemory(&ea, sizeof(ea));
    ea[0].grfAccessPermissions = KEY_READ;
    ea[0].grfAccessMode = SET_ACCESS;
    ea[0].grfInheritance= NO_INHERITANCE;
    ea[0].Trustee.TrusteeForm = TRUSTEE_IS_SID;
    ea[0].Trustee.TrusteeType = TRUSTEE_IS_WELL_KNOWN_GROUP;
    ea[0].Trustee.ptstrName  = pEveryoneSID;

    // Create a SID for the BUILTIN\Administrators group.
    bRes = AllocateAndInitializeSid(&SIDAuthNT, 2,
        SECURITY_BUILTIN_DOMAIN_RID,
        DOMAIN_ALIAS_RID_ADMINS,
        0, 0, 0, 0, 0, 0,
        &pAdminSID);
    ok(bRes, "AllocateAndInitializeSid Error %ld\n", GetLastError());
    if (!bRes)
    {
        skip("AdminSID not initialized. Terminating test\n");
        goto Cleanup;
    }

    // Initialize an EXPLICIT_ACCESS structure for an ACE.
    // The ACE will allow the Administrators group full access to the key.
    ea[1].grfAccessPermissions = KEY_ALL_ACCESS;
    ea[1].grfAccessMode = SET_ACCESS;
    ea[1].grfInheritance= NO_INHERITANCE;
    ea[1].Trustee.TrusteeForm = TRUSTEE_IS_SID;
    ea[1].Trustee.TrusteeType = TRUSTEE_IS_GROUP;
    ea[1].Trustee.ptstrName  = pAdminSID;

    // Create a new ACL that contains the new ACEs.
    dwRes = SetEntriesInAclW(_countof(ea), ea, NULL, &pACL);
    ok(dwRes == ERROR_SUCCESS, "SetEntriesInAcl Error %ld\n", GetLastError());
    if (dwRes != ERROR_SUCCESS)
        goto Cleanup;

    // Initialize a security descriptor.
    pSD = LocalAlloc(LPTR, SECURITY_DESCRIPTOR_MIN_LENGTH);
    ok(pSD != NULL, "LocalAlloc Error %ld\n", GetLastError());
    if (pSD == NULL)
        goto Cleanup;
 
    bRes = InitializeSecurityDescriptor(pSD, SECURITY_DESCRIPTOR_REVISION);
    ok(bRes, "InitializeSecurityDescriptor Error %ld\n", GetLastError());
    if (!bRes)
        goto Cleanup;

    // Add the ACL to the security descriptor.
    bRes = SetSecurityDescriptorDacl(pSD,
        TRUE,     // bDaclPresent flag
        pACL,
        FALSE);   // not a default DACL
    ok(bRes, "SetSecurityDescriptorDacl Error %ld\n", GetLastError());
    if (!bRes)
        goto Cleanup;

    // Initialize a security attributes structure.
    sa.lpSecurityDescriptor = pSD;
    sa.bInheritHandle = FALSE;

    // Use the security attributes to set the security descriptor
    // with an nlength that is 0.
    sa.nLength = 0;
    lRes = RegCreateKeyExW(HKEY_CURRENT_USER, L"mykey", 0, L"", 0,
            KEY_READ | KEY_WRITE, &sa, &hkSub, &dwDisposition);
    ok(lRes == ERROR_SUCCESS, "RegCreateKeyExW returned '%ld', expected 0", lRes);
    ok(dwDisposition == REG_CREATED_NEW_KEY, "Should have created NEW key\n");
    if (dwDisposition != REG_CREATED_NEW_KEY)
        goto Cleanup;

    // Test the -A function
    lRes = RegCreateKeyExA(HKEY_CURRENT_USER, "mykey", 0, "", 0,
            KEY_READ | KEY_WRITE, &sa, &hkSub, &dwDisposition);
    ok(lRes == ERROR_SUCCESS, "RegCreateKeyExA returned '%ld', expected 0", lRes);
    ok(dwDisposition == REG_OPENED_EXISTING_KEY, "Should have opened EXISTING key\n");
    if (dwDisposition != REG_OPENED_EXISTING_KEY)
        goto Cleanup;

    // Use the security attributes to set the security descriptor
    // with an nlength that is too short, but not 0.
    sa.nLength = sizeof(SECURITY_ATTRIBUTES) / 2;
    lRes = RegCreateKeyExW(HKEY_CURRENT_USER, L"mykey1", 0, L"", 0,
            KEY_READ | KEY_WRITE, &sa, &hkSub, &dwDisposition);
    ok(lRes == ERROR_SUCCESS, "RegCreateKeyExW returned '%ld', expected 0", lRes);
    ok(dwDisposition == REG_CREATED_NEW_KEY, "Should have created NEW key\n");
    if (dwDisposition != REG_CREATED_NEW_KEY)
        goto Cleanup;

    // Test the -A function
    lRes = RegCreateKeyExA(HKEY_CURRENT_USER, "mykey1", 0, "", 0,
            KEY_READ | KEY_WRITE, &sa, &hkSub, &dwDisposition);
    ok(lRes == ERROR_SUCCESS, "RegCreateKeyExA returned '%ld', expected 0", lRes);
    ok(dwDisposition == REG_OPENED_EXISTING_KEY, "Should have opened EXISTING key\n");
    if (dwDisposition != REG_OPENED_EXISTING_KEY)
        goto Cleanup;

    // Use the security attributes to set the security descriptor
    // with an nlength that is too long.
    sa.nLength = sizeof(SECURITY_ATTRIBUTES) + 10;
    lRes = RegCreateKeyExW(HKEY_CURRENT_USER, L"mykey2", 0, L"", 0,
            KEY_READ | KEY_WRITE, &sa, &hkSub, &dwDisposition);
    ok(lRes == ERROR_SUCCESS, "RegCreateKeyExW returned '%ld', expected 0", lRes);
    ok(dwDisposition == REG_CREATED_NEW_KEY, "Should have created NEW key\n");
    if (dwDisposition != REG_CREATED_NEW_KEY)
        goto Cleanup;

    // Test the -A function
    lRes = RegCreateKeyExA(HKEY_CURRENT_USER, "mykey2", 0, "", 0,
            KEY_READ | KEY_WRITE, &sa, &hkSub, &dwDisposition);
    ok(lRes == ERROR_SUCCESS, "RegCreateKeyExA returned '%ld', expected 0", lRes);
    ok(dwDisposition == REG_OPENED_EXISTING_KEY, "Should have opened EXISTING key\n");
    if (dwDisposition != REG_OPENED_EXISTING_KEY)
        goto Cleanup;

Cleanup:

    if (pEveryoneSID)
        FreeSid(pEveryoneSID);
    if (pAdminSID)
        FreeSid(pAdminSID);
    if (pACL)
        LocalFree(pACL);
    if (pSD)
        LocalFree(pSD);
    if (hkSub) 
        RegCloseKey(hkSub);

    // Delete the subkeys created for testing
    ErrorCode = RegDeleteKeyW(HKEY_CURRENT_USER, L"mykey");
    ok_dec(ErrorCode, ERROR_SUCCESS);

    ErrorCode = RegDeleteKeyW(HKEY_CURRENT_USER, L"mykey1");
    ok_dec(ErrorCode, ERROR_SUCCESS);

    ErrorCode = RegDeleteKeyW(HKEY_CURRENT_USER, L"mykey2");
    ok_dec(ErrorCode, ERROR_SUCCESS);
}
